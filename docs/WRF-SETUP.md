# Building WRF/WPS with a current toolchain

A practical guide to building WRF 4.6+ and WPS on a modern Linux system using
**current** versions of the dependencies, rather than the ancient versions
(HDF5 1.10.5, netCDF 4.7.2, MPICH 3.0.4, jasper 1.900.1, libpng 1.2.50) that the [UCAR tutorial](https://www2.mmm.ucar.edu/wrf/OnLineTutorial/compilation_tutorial.php#STEP1) and the [WRF forum](https://forum.mmm.ucar.edu/threads/full-wrf-and-wps-installation-example-gnu.12385/) guide recommend.

Why: those old libraries predate modern compilers (e.g. GCC 14+) by roughly a
decade and fail to compile with hard errors — for example the `H5CX_set_apl`
implicit-declaration error in HDF5 1.10.5's `H5Odeprec.c`. If your system ships
current versions, just use those.

The WRF/WPS build itself is the same everywhere; only the *dependency install*
step varies by system. The sections below cover distro packages, HPC modules,
and Spack, with Arch Linux as one of several options.

---

## 1. Dependencies

You need a Fortran compiler, a C compiler, the build tooling, and the scientific
libraries. Pick whichever dependency option matches your system.

Common requirements:

- Compilers: `gfortran`, `gcc`, `g++`
- Tooling: `make`, `cmake`, `ninja`, `git`, `perl`, `m4`, `flex`, `bison`
  (flex/bison are required for WRF's registry code generation)
- `tcsh` — WRF's build scripts invoke `csh`
- Libraries: `netcdf`, `netcdf-fortran`, `hdf5`, `zlib`, `libpng`, `libjpeg`,
  and an MPI implementation (`mpich` or `openmpi`) for distributed (dmpar) builds

### Option A — distro packages (simplest on a desktop)

**Arch Linux**

```bash
sudo pacman -S --needed \
  gcc gcc-fortran make cmake ninja git perl tcsh m4 flex bison \
  netcdf netcdf-fortran hdf5 \
  openmpi jasper libpng libjpeg-turbo zlib
```

Notes:
- On Arch the Fortran front-end is a separate package, **`gcc-fortran`** (it
  depends on `gcc`); there is no package literally named `gfortran`.
- **`openmpi`** provides `mpicc`/`mpifort` for distributed-memory (dmpar)
  builds and is the MPI implementation in the official repos. (`mpich` is
  AUR-only on Arch.)
- `libjpeg-turbo` is the drop-in replacement for the (removed) `libjpeg` package.

**Ubuntu / Debian**

```bash
sudo apt install \
  gfortran gcc g++ make cmake ninja-build git perl tcsh m4 flex bison \
  libnetcdf-dev libnetcdff-dev libhdf5-dev \
  mpi-default-dev zlib1g-dev libpng-dev libjpeg-dev
```

Notes:
- `tcsh` provides the `csh` that WRF requires.
- Leave out `libjasper-dev`: Ubuntu's jasper is 2.x and fails WPS's required
  1.900.x version — use WPS's bundled GRIB2 build instead (see the WPS section).

**Fedora**

```bash
sudo dnf install \
  gcc gcc-gfortran gcc-c++ make cmake ninja-build git perl tcsh m4 flex bison \
  netcdf-devel netcdf-fortran-devel hdf5-devel \
  openmpi-devel zlib-devel libpng-devel libjpeg-turbo-devel
```

Notes:
- After installing, load the MPI environment so `mpicc`/`mpifort` are on PATH
  (e.g. `source /etc/profile.d/modules.sh && module load mpi/$(arch)`).

Optional sanity check that the compiler and netCDF are present:

```bash
gfortran --version
nc-config --version
```

### Option B — environment modules (HPC clusters)

Most clusters provide the dependencies as modules:

```bash
module avail
module load netcdf netcdf-fortran hdf5 mpi <compiler>
```

`module load` typically sets `NETCDF`/`HDF5`/`MPI` env vars and puts the right
paths on `PATH`/`LD_LIBRARY_PATH`, so you can go straight to the build steps.

### Option C — build everything from source (Spack)

If the system has no usable packages or modules (or only wrong versions), use
Spack, which compiles every dependency (netCDF, HDF5, MPICH, jasper, ...) and WRF
itself with a single matching compiler:

```bash
module load <compiler>      # or use a system compiler directly
spack compiler find
spack install wrf@4.6 %gcc
spack install wps@4.5 %gcc
```

Spack's "everything with the same compiler" rule avoids the classic "all libs
must match" failures. (Hand-building the old HDF5-1.10.5 stack is not
recommended; if you must, see Troubleshooting.)

---

## 2. Get a current WRF (4.6+)

```bash
git clone --recurse-submodules https://github.com/wrf-model/WRF.git
cd WRF
```

---

## 3. Set the environment

```bash
export NETCDF=/usr            # point this at your netcdf install prefix
export WRF_DIR=$PWD
export CC=gcc
export CXX=g++
export FC=gfortran
export F77=gfortran
export FFLAGS="-m64 -fallow-argument-mismatch"
```

- `NETCDF` should be the prefix where netCDF is installed. On Arch and
  Ubuntu/Debian that is `/usr`; under Spack or modules it will be a distinct
  prefix (often already exported for you).
- No `grib2`/`jasper` env vars are needed: WPS 4.4+ builds its own GRIB2 stack
  (zlib/libpng/jasper) internally.

---

## 4. Build WRF

### Option A — CMake build (recommended for WRF 4.6+, most robust with system libs)

```bash
./configure_new -p "GNU" -- \
  -DCMAKE_Fortran_COMPILER=gfortran \
  -DUSE_MPI=ON -DWRF_CORE=ARW -DWRF_CASE=EM_REAL -DWRF_NESTING=BASIC

cmake --build _build -j 20
```

> **Interactive prompts:** `configure_new` prompts for `WRF_CORE`, `WRF_CASE`,
> and nesting even though you pass `-D...` flags (those are forwarded to the
> underlying cmake). Accept the matching defaults (`ARW` / `EM_REAL` / `BASIC`).

> **Known failure at ~32%** — `/lib/cpp: No such file or directory`:
> if the build dies around 32% with `make: *** Error 2` and no real compiler
> diagnostic, it's because WRF's GNU config hardcodes the C preprocessor at the
> Debian path `/lib/cpp`. On **usrmerged** systems (`/lib` → `/usr/lib`, i.e.
> Arch and Ubuntu 23.04+), `cpp` lives at `/usr/bin/cpp` and `/lib/cpp` doesn't
> resolve. Fix the generated config, reconfigure, and rebuild:
> ```bash
> sed -i 's|set( CMAKE_C_PREPROCESSOR "/lib/cpp" )|set( CMAKE_C_PREPROCESSOR "/usr/bin/cpp" )|' _build/wrf_config.cmake
> cmake -S . -B _build
> cmake --build _build -j 20
> ```
> For a permanent fix that survives reconfigure, also patch
> `arch/configure.defaults` (GNU gfortran stanza, ~line 811): change
> `CPP = /lib/cpp CONFIGURE_CPPFLAGS` to `CPP = cpp CONFIGURE_CPPFLAGS`.

### Option B — classic configure (closest to the tutorial)

```bash
./configure   # pick dmpar (distributed, recommended) or serial GNU option, then nesting
./compile em_real >& compile.log
tail -f compile.log
```

### Verify the build

The executables are ELF binaries on Linux; there is no `.exe` involved. The
naming convention differs by build method:

- **CMake build (Option A):** binaries land in `_build/main/` **without** a
  `.exe` suffix: `_build/main/{wrf, real, ndown, tc}`.
- **Classic build (Option B):** binaries land in `main/` and **do** carry a
  `.exe` suffix by WRF's own convention (`main/wrf.exe`, `main/real.exe`,
  `main/ndown.exe`, `main/tc.exe`) — still normal Linux executables.

---

## 5. Build WPS (WRF Preprocessing System)

WPS is the suite that prepares input data for WRF (see the pipeline at the end
of this section). Use WPS's **CMake** build (`configure_new`) to match the
CMake-built WRF.

### Prerequisite: install the WRF CMake package

WPS locates WRF through its **installed** CMake package, not the WRF source
tree. So first install the WRF build (Step 4 Option A) into its prefix:

```bash
cd ../WRF
cmake --install _build    # puts WRFConfig.cmake into install/lib/cmake/WRF/
```

WPS then finds it automatically at `../WRF/install` (no `WRF_DIR` needed).

### Configure and build

```bash
cd ../WPS
git clone https://github.com/wrf-model/WPS.git
cd WPS

export NETCDF=/usr        # same prefix as the WRF build

./configure_new -p "gfortran"
# answer the prompts:
#   [GRIB2] Build GRIB2 libraries (zlib, libpng, JasPer) from source?  -> y
#       (most distros' jasper is 2.x and fails WPS's required 1.900.x
#        version, so build the bundled one)
#   [WRF]   Attempt to find compiled WRF model?                        -> y
cmake --build _build -j 20
cmake --install _build
```

If `configure_new` is non-interactive, pipe the two answers (GRIB2 then WRF):

```bash
printf 'y\n\n' | ./configure_new -p "gfortran"
```

On success the three programs are in `install/bin/` (WPS links `*.exe` symlinks
to them by convention, even on Linux — still ELF binaries):

```bash
ls -la install/bin/geogrid install/bin/ungrib install/bin/metgrid
```

> **`Could NOT find Jasper ... required range 1.900.1...1.900.29` while
> configuring**: you got a system-jasper build (`BUILD_EXTERNALS=OFF`). Delete
> `_build` (its stale cache pins the system lib paths) and reconfigure answering
> the GRIB2 prompt `y` so it builds the bundled jasper-1.900.x.

> **Legacy `./configure` says "WRF hasn't been compiled yet"**: the legacy WPS
> configure looks for `${WRF_DIR}/external/io_netcdf/libwrfio_nf.a`, a
> legacy-build artifact a CMake WRF build doesn't produce. Use `configure_new`
> (above) instead.

What the three programs do (the standard WRF preprocessing pipeline):

```
geogrid  → geo_em.d01.nc        (domain grid + static terrain/land-use)
ungrib   → FILE:YYYY-MM-DD_HH   (meteorological fields from GRIB input)
metgrid  → met_em.d01.*.nc      (interpolate meteorology onto the geogrid domain)
```

Those `met_em` files feed `real.exe` (built with WRF) to produce the final
`wrfinput_d01`/`wrfbdy_d01` initial and boundary conditions for `wrf.exe`.

---

## 6. Run WRF — idealized test case (no data downloads)

Before dealing with real weather data, it's worth confirming the whole
build/init/run pipeline works with an **idealized** case (a synthetic atmosphere
defined by the namelist, no WPS/geographic/GRIB data needed). This is the
fastest way to produce real `wrfout` files and check the install end-to-end.

> **Gotcha — build mode:** the `WRF_CASE` you configure controls what WRF can
> run. `EM_REAL` (the default) builds `real`/`wrf` for *real data* — its
> `real.exe` looks for `met_em.*.nc` files and the idealized cases are **not**
> compiled in. To run an idealized case you need a separate build configured
> with the case as `WRF_CASE` (e.g. `EM_B_WAVE`, `EM_QUARTER_SS`).

### Build the idealized case into its own directory

Keep your real-data (`EM_REAL`) build intact by using a separate build dir
(`-d _build_ideal`). `configure_new` is interactive; the piped answers below
are `WRF_CORE=ARW (enter)`, `WRF_NESTING=BASIC (enter)`, `WRF_CASE=EM_B_WAVE
(5)`, `MPI=no`, `OpenMP=no`, `extra options=no`:

```bash
printf '\n\n5\nn\n\n\n' | ./configure_new -d _build_ideal -p "GNU" -- \
  -DCMAKE_Fortran_COMPILER=gfortran

# apply the /lib/cpp fix (usrmerged systems) to this build too
sed -i 's|set( CMAKE_C_PREPROCESSOR "/lib/cpp" )|set( CMAKE_C_PREPROCESSOR "/usr/bin/cpp" )|' _build_ideal/wrf_config.cmake

# you edited wrf_config.cmake AFTER configure ran, so re-run cmake, then build
cmake -S . -B _build_ideal
cmake --build _build_ideal -j 20
```

> **Gotcha — `/lib/cpp` fix requires a re-run of cmake:** the preprocessor path
> is baked into the build rules at configure time. Editing `wrf_config.cmake`
> after `configure_new` won't take effect until you re-run `cmake -S . -B
> _build_ideal`.

> **Gotcha — prompt count varies:** the `[DM] Use MPI?` prompt only appears if
> an MPI compiler (`mpif90`) is on `PATH`. If yours is missing, the piped
> answers shift. If in doubt, run `./configure_new` interactively in a terminal
> instead of piping.

### Set up and run the case

The idealized build produces `ideal` (initializer) and `wrf` in
`_build_ideal/main/`. Note these are named without a `.exe` suffix, and the
initializer is `ideal` — not `real`.

```bash
cd test/em_b_wave

# link the executables (keep the .exe names WRF scripts expect)
ln -sf ../../_build_ideal/main/wrf   wrf.exe
ln -sf ../../_build_ideal/main/ideal ideal.exe

# link the runtime data files (read-only; safe to symlink the whole dir)
ln -sf ../../run/* .
```

Now write a small single-domain `namelist.input` (see below) and run the
initializer, then the model:

```bash
./ideal.exe      # creates wrfinput_d01  ->  "SUCCESS COMPLETE IDEAL INIT"
./wrf.exe        # creates wrfout_d01_*  ->  "SUCCESS COMPLETE WRF"
```

> **Gotcha — `em_b_wave` needs `ideal.exe` first:** running `wrf.exe` directly
> fails with `error opening wrfinput_d01 for reading`. The idealized
> initializer must run once first to create `wrfinput_d01`.

Verify the result:

```bash
ls -la wrfout_*
# text peek: ncdump -h wrfout_d01_0001-01-01_00:00:00 | less
```

A minimal single-domain `namelist.input` (6 h, 41×81, 65 levels):

```fortran
 &time_control
 run_days=0, run_hours=6, run_minutes=0, run_seconds=0,
 start_year=0001, start_month=01, start_day=01, start_hour=00,
 end_year=0001, end_month=01, end_day=01, end_hour=06,
 history_interval=360, frames_per_outfile=1000,
 restart=.false., io_form_history=2, io_form_restart=2,
 io_form_input=2, io_form_boundary=2,
 /
 &domains
 time_step=600, max_dom=1,
 s_we=1, e_we=41, s_sn=1, e_sn=81, s_vert=1, e_vert=65,
 dx=100000, dy=100000, ztop=16000, grid_id=1, parent_id=0,
 i_parent_start=0, j_parent_start=0, parent_grid_ratio=1,
 parent_time_step_ratio=1, feedback=1, smooth_option=0,
 /
 &physics
 mp_physics=0, ra_lw_physics=0, ra_sw_physics=0, radt=30,
 sf_sfclay_physics=0, sf_surface_physics=0, bl_pbl_physics=0,
 bldt=0, cu_physics=0, cudt=5,
 /
 &fdda
 /
 &dynamics
 hybrid_opt=0, rk_ord=3, diff_opt=1, km_opt=1, damp_opt=0,
 zdamp=4000., dampcoef=0.01, khdif=0, kvdif=0,
 smdiv=0.1, emdiv=0.01, epssm=0.1, time_step_sound=4,
 h_mom_adv_order=5, v_mom_adv_order=3, h_sca_adv_order=5,
 v_sca_adv_order=3, non_hydrostatic=.true.,
 /
 &bdy_control
 periodic_x=.true., symmetric_xs=.false., symmetric_xe=.false.,
 open_xs=.false., open_xe=.false., periodic_y=.false.,
 symmetric_ys=.true., symmetric_ye=.true., open_ys=.false.,
 open_ye=.false.,
 /
 &grib2
 /
 &namelist_quilt
 nio_tasks_per_group=0, nio_groups=1,
 /
 &ideal
 ideal_case=7
 /
```

Other available idealized cases (pick `WRF_CASE`/`ideal_case` accordingly):
`EM_QUARTER_SS`, `EM_HILL2D_X`, `EM_SQUALL2D_X`, `EM_SEABREEZE2D_X`,
`EM_LES`, `EM_HELDSUAREZ`, `EM_TROPICAL_CYCLONE`, `EM_FIRE`. Each has a
`test/em_*` directory with its own `namelist.input` to adapt.

---

## Troubleshooting

- **Build dies at ~32% with `/lib/cpp: No such file or directory`**: WRF's GNU
  config hardcodes the C preprocessor at the Debian path `/lib/cpp`. On
  usrmerged systems (`/lib` → `/usr/lib`; Arch, Ubuntu 23.04+) that path doesn't
  exist. Fix `_build/wrf_config.cmake` to use `/usr/bin/cpp` and reconfigure
  (see Step 4 Option A). For permanence, patch
  `CPP = /lib/cpp ...` → `CPP = cpp ...` in `arch/configure.defaults`.

- **WPS GRIB2/jasper complaints with legacy WRF configure**: modern distros
  (Arch, Ubuntu, Fedora) ship jasper 2.x/3.x and current libpng. The *legacy*
  `./configure` build hardcodes expectations for old versions. Prefer the CMake
  build (`configure_new`) or `./configure --build-grib2-libs` in WPS. If you must
  use legacy WRF GRIB2 support with current jasper, help it find the libs via
  `JASPERLIB`/`JASPERINC`.

- **If you instead hand-build old HDF5 1.10.5**: you must add
  `#include "H5CXprivate.h"` to `src/H5Odeprec.c` (it calls `H5CX_set_apl`
  without the header, which is a hard error on GCC 14+), and put `-fcommon`
  in `CFLAGS`, not `CPPFLAGS`. But this is not recommended — just use HDF5 1.14.x
  (or your distro's packaged version).
