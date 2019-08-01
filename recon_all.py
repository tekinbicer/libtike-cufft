import os
import signal
import sys

import cupy as cp
import dxchange
import numpy as np
import scipy
import ptychotomo as pt
import time
import h5py
if __name__ == "__main__":

    if (len(sys.argv) < 2):
        igpu = 0
    else:
        igpu = np.int(sys.argv[1])

    cp.cuda.Device(igpu).use()  # gpu id to use
    # use cuda managed memory in cupy
    pool = cp.cuda.MemoryPool(cp.cuda.malloc_managed)
    cp.cuda.set_allocator(pool.malloc)

    # Model parameters
    prbshiftx = 300/28.17822322520485  # np.int(0.5667*113)
    prbshifty = prbshiftx*2/3.0  # np.int(0.5667*113)


    for k in range(105,145):        
        ntheta = 1
        prbsize = 128  # probe size
        
        det = [128, 128]  # detector size
        model = 'poisson'  # minimization funcitonal (poisson,gaussian)
        piter = 512  # ptychography iterations
        
            
        # read probe            
        name = '/mxn/home/viknik/ptychocg_real/Piller_Step_Recon/Pillar_Step_scan110_s128_i50_recon.h5'# % (k+10)
        prbfile = h5py.File(name,'r')
        prb = cp.array(prbfile['/probes/magnitude'][:128,:].astype('float32')*np.exp(1j*prbfile['/probes/phase'][:128,:].astype('float32')))
        # read data                
        datafile = h5py.File('/mxn/home/viknik/ptychocg_real/doga_scan_orig.h5','r')
        name = '/exchange/data/%d' % (k+5)
        print(name)
        data = cp.array(np.expand_dims(datafile[name] , axis=0)).astype('float32')
        data = cp.fft.fftshift(data, axes=[2,3])
        print("max intensity on the detector: ", np.amax(data))
        
        n = np.int(prbshiftx*data.shape[1]/21+128-prbshiftx+1)
        nz = np.int(prbshifty*21+128-prbshifty+1)
        print(n,nz)
        print(data.shape[1]/42)
        
        scan = cp.array(pt.scanner3([ntheta,nz,n], prbshiftx,
                                    prbshifty, prbsize, spiral=0, randscan=False, save=True))
        print('scan shape',scan.shape)                                
        # Class gpu solver
        slv = pt.Solver(prb, scan, det, nz, n)

        def signal_handler(sig, frame):  # Free gpu memory after SIGINT, SIGSTSTP
            slv = []
            sys.exit(0)
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTSTP, signal_handler)

        # ######### gen synthetic data
        # prbfile = 'probes-010'
        # prbid = 1
        # amp = dxchange.read_tiff(
        #     'data/Cryptomeria_japonica-1024.tif').astype('float32')[512:512+nz,512:512+n]/255.0
        # ang = dxchange.read_tiff(
        #     'data/Erdhummel_Bombus_terrestris-1024.tif').astype('float32')[512:512+nz,512:512+n]/255.0*np.pi
        # psi = amp*np.exp(1j*ang)    
        # psi = cp.array(np.expand_dims(psi, axis=0))    
        # data = slv.fwd_ptycho_batch(psi)
        # # [x1,x2] = meshgrid(np.arange())
        # # dxchange.write_tiff(data,'tmpdata')
        
        ###### or load data??
        
        # CG scheme    
        init = cp.ones([1, nz, n]).astype('complex64')*0.3
        psi = slv.cg_ptycho_batch(data, init, piter, model)
        # Save result
        name = str(model)+'%.3d' % (k)
        dxchange.write_tiff(cp.angle(
            psi).get().astype('float32'),  'psiangprb110/psiang'+name, overwrite=False)
        dxchange.write_tiff(
            cp.abs(psi).get().astype('float32'),  'psiampprb110/psiamp'+name)