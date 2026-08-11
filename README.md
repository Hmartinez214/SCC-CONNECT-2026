# SCC-CONNECT-2026
UCSC SCC Connect Repo 

run: source setup\_env.sh 

Using HPC-Tools (already configured with the setup)
- basic\_cmake\_tarball : builds and installs library from tarball link
- basic\_cmake\_github : builds and installs library from github link
- add\_build : add installed folder to PATH (searches for lib/library folders within)
- remove\_build : remove build from path
- EX: basic_cmake_tarball https://download.open-mpi.org/release/open-mpi/v5.0/openmpi-5.0.9.tar.gz -n OpenMPI -configure ON --cmake-args --enable-mpi-thread-multiple
- EX: basic_cmake_github https://github.com/OpenMathLib/OpenBLAS.git -n OpenBLAS
- EX: add\_build $INSTALL\_DIR/OpenMPI



libs will contain all the external code we need. HPC-Tools will help compile libraries (spack could also be used)
- libs/clone stores all the cloned code
- libs/build stores the build artifacts
- libs/opt contains the installed libraries and headers (use add\_build)
- These directories are specified by INSTALL\_DIR, BUILD\_DIR, CLONE\_DIR environment variables

