#!/bin/bash 
#SBATCH -N 1
#SBATCH -q regular
#SBATCH -t 02:00:00
#SBATCH -J test
#SBATCH -A m4007   #userdependent
#SBATCH -L scratch,cfs
#SBATCH -C cpu
#SBATCH --tasks-per-node=64 #user needs to experiment this value

pwd
ntile=4  #number of OpenMP threads per MPI task
#need to set the "numtiles" variable in the wrf namelist (namelist.input) to be the same 

wrfexe="/global/homes/h/hmartine/SCC-CONNECT-2026/libs/clone/WRF/main/wrf.exe" #recommend to save the executable in global common or scratch space
rundir="/pscratch/sd/h/hmartine/wrf/benchmark/v4_bench_conus12km" #where to run WRF; user needs to change this

#Modules --------------------------------------------------------------------
#general modules
module load cpu  
module load PrgEnv-gnu 

#module for WRF file I/O
#order of loading matters!
module load cray-hdf5  #required to load netcdf library
module load cray-netcdf 
module load cray-parallel-netcdf

#OpenMP settings:
export OMP_NUM_THREADS=$ntile
export OMP_PLACES=threads  #"true" when not using multiple OpenMP threads (i.e., ntile=1)
export OMP_PROC_BIND=spread
export OMP_STACKSIZE=64M  #increase memory segment to store local variables, needed by each thread

cd $rundir

#run simulation
srun -n 64 -c 4 --cpu_bind=cores ${wrfexe}

#rename and save the process 0 out and err files
cp rsl.error.0000 rsl.error_0_$SLURM_JOBID
cp rsl.out.0000 rsl.out_0_$SLURM_JOBID