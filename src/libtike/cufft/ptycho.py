"""A module for ptychography solvers.

This module implements ptychographic solvers which all inherit from a
ptychography base class. The base class implements the forward and adjoint
ptychography operators and manages GPU memory.

Solvers in this module are Python context managers which means they should be
instantiated using a with-block. e.g.

```python
# load data and such
data = cp.load(...)
# instantiate the solver with memory allocation related parameters
with CustomPtychoSolver(...) as solver:
    # call the solver with solver specific parameters
    result = solver.run(data, ...)
# solver memory is automatically freed at with-block exit
```

Context managers are capable of gracefully handling interruptions (CTRL+C).

"""

import signal
import sys
import warnings

import cupy as cp
import numpy as np

from libtike.cufft.ptychofft import ptychofft


class PtychoCuFFT(ptychofft):
    """Base class for ptychography solvers using the cuFFT library.

    This class is a context manager which provides the basic operators required
    to implement a ptychography solver. It also manages memory automatically,
    and provides correct cleanup for interruptions or terminations.

    Attribtues
    ----------
    nscan : int
        The number of scan positions at each angular view.
    nprb : int
        The pixel width and height of the probe illumination.
    ndetx, ndety : int
        The pixel width and height of the detector.
    ntheta : int
        The number of angular partitions of the data.
    n, nz : int
        The pixel width and height of the reconstructed grid.
    ptheta : int
        The number of angular partitions to process together
        simultaneously.
    """

    def __init__(self, nscan, nprb, ndetx, ndety, ntheta, nz, n, ptheta, igpu):
        """Please see help(PtychoCuFFT) for more info."""
        cp.cuda.Device(igpu).use()  # gpu id to use
        # set cupy to use unified memory
        pool = cp.cuda.MemoryPool(cp.cuda.malloc_managed)
        cp.cuda.set_allocator(pool.malloc)

        super().__init__(ptheta, nz, n, nscan, ndetx, ndety, nprb)
        self.ntheta = ntheta  # number of projections

    def __enter__(self):
        """Return self at start of a with-block."""
        return self

    def __exit__(self, type, value, traceback):
        """Free GPU memory due at interruptions or with-block exit."""
        self.free()

    def fwd_ptycho(self, psi, scan, prb):
        """Ptychography transform (FQ)."""
        assert psi.dtype == cp.complex64, f"{psi.dtype}"
        assert scan.dtype == cp.float32, f"{scan.dtype}"
        assert prb.dtype == cp.complex64, f"{prb.dtype}"
        res = cp.zeros([self.ptheta, self.nscan, self.ndety, self.ndetx],
                       dtype='complex64')
        self.fwd(res.data.ptr, psi.data.ptr, scan.data.ptr, prb.data.ptr)
        return res

    def fwd_ptycho_batch(self, psi, scan, prb):
        """Batch of Ptychography transform (FQ)."""
        assert psi.dtype == np.complex64, f"{psi.dtype}"
        assert scan.dtype == np.float32, f"{scan.dtype}"
        assert prb.dtype == np.complex64, f"{prb.dtype}"
        data = np.zeros([self.ntheta, self.nscan, self.ndety,
                         self.ndetx], dtype='complex64')
        # angle partitions in ptychography
        for k in range(0, self.ntheta // self.ptheta):
            ids = np.arange(k * self.ptheta, (k + 1) * self.ptheta)
            # copy to GPU
            psi_gpu = cp.array(psi[ids])
            scan_gpu = cp.array(scan[:, ids])
            prb_gpu = cp.array(prb[ids])
            # compute part on GPU
            data_gpu = self.fwd_ptycho(psi_gpu, scan_gpu, prb_gpu)
            # copy to CPU
            data[ids] = data_gpu.get()            
        return data

    def adj_ptycho(self, data, scan, prb):
        """Adjoint ptychography transform (Q*F*)."""
        assert data.dtype == cp.complex64, f"{data.dtype}"
        assert scan.dtype == cp.float32, f"{scan.dtype}"
        assert prb.dtype == cp.complex64, f"{prb.dtype}"
        res = cp.zeros([self.ptheta, self.nz, self.n], dtype='complex64')
        flg = 0  # compute adjoint operator with respect to object
        self.adj(res.data.ptr, data.data.ptr, scan.data.ptr, prb.data.ptr, flg)
        return res

    def adj_ptycho_batch(self, data, scan, prb):
        """Batch of Ptychography transform (FQ)."""
        assert data.dtype == np.complex64, f"{data.dtype}"
        assert scan.dtype == np.float32, f"{scan.dtype}"
        assert prb.dtype == np.complex64, f"{prb.dtype}"
        psi = np.zeros([self.ntheta, self.nz, self.n], dtype='complex64')
        # angle partitions in ptychography
        for k in range(0, self.ntheta // self.ptheta):
            ids = np.arange(k * self.ptheta, (k + 1) * self.ptheta)
            # copy to GPU
            data_gpu = cp.array(data[ids])
            scan_gpu = cp.array(scan[:, ids])
            prb_gpu = cp.array(prb[ids])
            # compute part on GPU
            psi_gpu = self.adj_ptycho(data_gpu, scan_gpu, prb_gpu)
            # copy to CPU
            psi[ids] = psi_gpu.get()
        return psi

    def adj_ptycho_prb(self, data, scan, psi):
        """Adjoint ptychography probe transform (O*F*), object is fixed."""
        assert data.dtype == cp.complex64, f"{data.dtype}"
        assert scan.dtype == cp.float32, f"{scan.dtype}"
        assert psi.dtype == cp.complex64, f"{psi.dtype}"
        res = cp.zeros([self.ptheta, self.nprb, self.nprb], dtype='complex64')
        flg = 1  # compute adjoint operator with respect to probe
        self.adj(psi.data.ptr, data.data.ptr, scan.data.ptr, res.data.ptr, flg)
        return res

    def adj_ptycho_batch_prb(self, data, scan, psi):
        """Batch of Ptychography transform (FQ)."""
        assert data.dtype == np.complex64, f"{data.dtype}"
        assert scan.dtype == np.float32, f"{scan.dtype}"
        assert psi.dtype == np.complex64, f"{psi.dtype}"
        prb = np.zeros([self.ntheta, self.nprb, self.nprb], dtype='complex64')
        # angle partitions in ptychography
        for k in range(0, self.ntheta // self.ptheta):
            ids = np.arange(k * self.ptheta, (k + 1) * self.ptheta)
            # copy to GPU
            data_gpu = cp.array(data[ids])
            scan_gpu = cp.array(scan[:, ids])
            psi_gpu = cp.array(psi[ids])
            # compute part on GPU
            prb_gpu = self.adj_ptycho_prb(data_gpu, scan_gpu, psi_gpu)
            # copy to CPU
            prb[ids] = prb_gpu.get()
        return prb

    def run(self, data, psi, scan, prb, **kwargs):
        """Placehold for a child's solving function."""
        raise NotImplementedError("Cannot run a base class.")

    def run_batch(self, data, psi, scan, prb, **kwargs):
        """Run by dividing the work into batches."""
        assert prb.ndim == 3, "prb needs 3 dimensions, not %d" % prb.ndim

        psi = psi.copy()
        prb = prb.copy()

        # angle partitions in ptychography
        for k in range(0, self.ntheta // self.ptheta):
            ids = np.arange(k * self.ptheta, (k + 1) * self.ptheta)
            # copy to GPU
            psi_gpu = cp.array(psi[ids])
            scan_gpu = cp.array(scan[:, ids])
            prb_gpu = cp.array(prb[ids])
            data_gpu = cp.array(data[ids])
            # solve cg ptychography problem for the part
            result = self.run(
                data_gpu,
                psi_gpu,
                scan_gpu,
                prb_gpu,
                **kwargs,
            )
            psi[ids], prb[ids] = result['psi'].get(), result['prb'].get()
        return {
            'psi': psi,
            'prb': prb,
        }


class CGPtychoSolver(PtychoCuFFT):
    """Solve the ptychography problem using congujate gradient."""

    @staticmethod
    def line_search(f, x, d, step_length=1, step_shrink=0.5):
        """Return a new step_length using a backtracking line search.

        https://en.wikipedia.org/wiki/Backtracking_line_search

        Parameters
        ----------
        f : function(x)
            The function being optimized.
        x : vector
            The current position.
        d : vector
            The search direction.

        """
        assert step_shrink > 0 and step_shrink < 1
        m = 0  # Some tuning parameter for termination
        fx = f(x)  # Save the result of f(x) instead of computing it many times
        # Decrease the step length while the step increases the cost function
        while f(x + step_length * d) > fx + step_shrink * m:
            if step_length < 1e-32:
                warnings.warn("Line search failed for conjugate gradient.")
                return 0
            step_length *= step_shrink
        return step_length

    def run(
            self,
            data,
            psi,
            scan,
            prb,
            piter,
            model='gaussian',
            recover_prb=False,
    ):
        """Conjugate gradients for ptychography.

        Parameters
        ----------
        model : str gaussian or poisson
            The noise model to use for the gradient.
        piter : int
            The number of gradient steps to take.
        recover_prb : bool
            Whether to recover the probe or assume the given probe is correct.

        """
        assert prb.ndim == 3, "prb needs 3 dimensions, not %d" % prb.ndim

        # minimization functional
        def minf(fpsi):
            if model == 'gaussian':
                f = cp.linalg.norm(cp.abs(fpsi) - cp.sqrt(data))**2
            elif model == 'poisson':
                f = cp.sum(
                    cp.abs(fpsi)**2 - 2 * data * cp.log(cp.abs(fpsi) + 1e-32))
            return f

        print("# congujate gradient parameters\n"
              "iteration, step size object, step size probe, function min"
              )  # csv column headers
        gammaprb = 0
        for i in range(piter):
            # 1) object retrieval subproblem with fixed probe
            # forward operator
            fpsi = self.fwd_ptycho(psi, scan, prb)
            # take gradient
            if model == 'gaussian':
                gradpsi = self.adj_ptycho(
                    fpsi - cp.sqrt(data) * cp.exp(1j * cp.angle(fpsi)),
                    scan,
                    prb,
                ) / (cp.max(cp.abs(prb))**2)
            elif model == 'poisson':
                gradpsi = self.adj_ptycho(
                    fpsi - data * fpsi / (cp.abs(fpsi)**2 + 1e-32),
                    scan,
                    prb,
                ) / (cp.max(cp.abs(prb))**2)
            # Dai-Yuan direction
            if i == 0:
                dpsi = -gradpsi
            else:
                dpsi = -gradpsi + (
                    cp.linalg.norm(gradpsi)**2 /
                    (cp.sum(cp.conj(dpsi) * (gradpsi - gradpsi0))) * dpsi)
            gradpsi0 = gradpsi
            # line search
            fdpsi = self.fwd_ptycho(dpsi, scan, prb)
            gammapsi = self.line_search(minf, fpsi, fdpsi)
            # update psi
            psi = psi + gammapsi * dpsi

            if (recover_prb):
                # 2) probe retrieval subproblem with fixed object
                # forward operator
                fprb = self.fwd_ptycho(psi, scan, prb)
                # take gradient
                if model == 'gaussian':
                    gradprb = self.adj_ptycho_prb(
                        fprb - cp.sqrt(data) * cp.exp(1j * cp.angle(fprb)),
                        scan,
                        psi,
                    ) / cp.max(cp.abs(psi))**2 / self.nscan
                elif model == 'poisson':
                    gradprb = self.adj_ptycho_prb(
                        fprb - data * fprb / (cp.abs(fprb)**2 + 1e-32),
                        scan,
                        psi,
                    ) / cp.max(cp.abs(psi))**2 / self.nscan
                # Dai-Yuan direction
                if (i == 0):
                    dprb = -gradprb
                else:
                    dprb = -gradprb + (
                        cp.linalg.norm(gradprb)**2 /
                        (cp.sum(cp.conj(dprb) * (gradprb - gradprb0))) * dprb)
                gradprb0 = gradprb
                # line search
                fdprb = self.fwd_ptycho(psi, scan, dprb)
                gammaprb = self.line_search(minf, fprb, fdprb)
                # update prb
                prb = prb + gammaprb * dprb

            # check convergence
            if (np.mod(i, 8) == 0):
                fpsi = self.fwd_ptycho(psi, scan, prb)
                print("%4d, %.3e, %.3e, %.7e" %
                      (i, gammapsi, gammaprb, minf(fpsi)))

        return {
            'psi': psi,
            'prb': prb,
        }