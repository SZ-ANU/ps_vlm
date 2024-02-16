#!/usr/bin/env python3
"""

"""

import scipy.io as sio
import numpy as np
import sys
import os

from scipy.signal import fftconvolve, gaussian, convolve2d
from scipy.fft import fft2, ifft2, fftshift, ifftshift
from typing import Any, Tuple, Optional, List
from joblib import dump, load
from pathlib import Path


np.set_printoptions(precision=4, suppress=True, linewidth=120, threshold=10)


class dotdict(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__
    __dir__ = dict.keys

    def __getstate__(self):
        return self.__dict__

    def __setstate__(self, d):
        self.__dict__.update(d)


def log(msg: str) -> None:
    """Prints a message to stderr."""
    print(msg, file=sys.stderr)


def datenum(dates):
    # TODO: Replace with datetime
    from datetime import datetime as dt

    dts = [dt.utcfromtimestamp((d - np.datetime64("1970-01-01T00:00:00Z")) / np.timedelta64(1, "s")) for d in dates]
    return np.array(
        [366 + d.toordinal() + (d - dt.fromordinal(d.toordinal())).total_seconds() / (24 * 60 * 60) for d in dts],
        dtype=np.float64,
    )


def stamps_save(fn: str, *args, **kwargs):
    if fn.endswith(".pkl"):
        fn = Path(fn)
    else:
        fn = Path(f"{fn}.pkl")
    if len(args) > 0:
        dump(args[0], fn)
    else:
        dump(dotdict(kwargs), fn)


def stamps_load(fn: str) -> Any:
    if fn.endswith(".pkl"):
        fn = Path(fn)
    else:
        fn = Path(f"{fn}.pkl")
    return load(fn)


def loadmat(fname: Path) -> Any:
    """Loads a .mat file."""

    mat = sio.loadmat(str(fname), squeeze_me=True)
    kvs = {k: v for k, v in mat.items() if not k.startswith("__")}

    for k in kvs.keys():
        try:
            v = kvs[k]
            if isinstance(v, np.ndarray) and v.size == 1:
                v = v.flat[0]
        except IndexError:
            pass
        kvs[k] = v

    return kvs


def llh2local_alternate(llh, origin):
    """
    Converts from longitude and latitude to local coordinates given an origin.
    llh (lon, lat, height) and origin should be in decimal degrees.
    Note that heights are ignored and that xy is in km.
    """

    import pymap3d as pm

    xy = np.zeros((2, llh.shape[1]))
    for i, (lon, lat) in enumerate(llh.T):
        x, y, z = pm.geodetic2enu(lat, lon, 0, origin[1], origin[0], 0)
        xy[0, i] = x
        xy[1, i] = y
    return xy / 1000


def llh2local(llh, origin):
    """
    Converts from longitude and latitude to local coordinates given an origin.
    llh (lon, lat, height) and origin should be in decimal degrees.
    Note that heights are ignored and that xy is in km.
    """

    # Set ellipsoid constants (WGS84)
    a = 6378137.0  # semi-major axis
    e = 0.08209443794970  # eccentricity

    # Convert to radians
    llh = np.deg2rad(llh)
    origin = np.deg2rad(origin)

    # Initialize xy array
    xy = np.zeros((2, llh.shape[1]))

    # Do the projection
    z = llh[1, :] != 0
    dlambda = llh[0, z] - origin[0]

    # calculates the meridional arc length M for each point and the origin M0, considering
    # the ellipsoidal shape of the Earth. This involves a series expansion that accounts
    # for the ellipsoid's eccentricity.

    M = a * (
        (1 - e**2 / 4 - 3 * e**4 / 64 - 5 * e**6 / 256) * llh[1, z]
        - (3 * e**2 / 8 + 3 * e**4 / 32 + 45 * e**6 / 1024) * np.sin(2 * llh[1, z])
        + (15 * e**4 / 256 + 45 * e**6 / 1024) * np.sin(4 * llh[1, z])
        - (35 * e**6 / 3072) * np.sin(6 * llh[1, z])
    )

    M0 = a * (
        (1 - e**2 / 4 - 3 * e**4 / 64 - 5 * e**6 / 256) * origin[1]
        - (3 * e**2 / 8 + 3 * e**4 / 32 + 45 * e**6 / 1024) * np.sin(2 * origin[1])
        + (15 * e**4 / 256 + 45 * e**6 / 1024) * np.sin(4 * origin[1])
        - (35 * e**6 / 3072) * np.sin(6 * origin[1])
    )

    N = a / np.sqrt(1 - e**2 * np.sin(llh[1, z]) ** 2)
    E = dlambda * np.sin(llh[1, z])

    xy[0, z] = N / np.tan(llh[1, z]) * np.sin(E)
    xy[1, z] = M - M0 + N / np.tan(llh[1, z]) * (1 - np.cos(E))

    # Handle special case of latitude = 0
    dlambda = llh[0, ~z] - origin[0]
    xy[0, ~z] = a * dlambda
    xy[1, ~z] = -M0

    # Convert to km
    xy = xy / 1000

    return xy



def getparm(parmname: Optional[str] = None, verbose: bool = False) -> Tuple[Optional[Any], Optional[str]]:
    """Retrieves a parameter value from parameter files."""

    # TODO: Add support for other file formats (txt?)

    def pprint(k, v):
        if isinstance(v, str):
            log(f"{k} = '{v}'")
        else:
            log(f"{k} = {v}")

    # Load global parameters
    parmfile = Path("parms.mat").absolute()
    if parmfile.exists():
        parms = loadmat(parmfile)
    elif (parmfile.parent.parent / parmfile.name).exists():
        parmfile = parmfile.parent.parent / parmfile.name
        parms = loadmat(parmfile)
    else:
        raise FileNotFoundError(f"`{parmfile}` not found")

    # Load local parameters, if available
    localparmfile = Path("localparms.mat")
    if localparmfile.exists():
        localparms = loadmat(localparmfile)
    else:
        localparms = {"Created": "today"}  # Placeholder for creation date

    value = None
    if parmname is None:
        for k, v in parms.items():
            pprint(k, v)
        if len(localparms) > 1:
            log(localparms)
    else:
        # Find the parameter in global or local parameters
        parmnames = [k for k in parms if not k.startswith("__")]
        matches = [pn for pn in parmnames if pn.startswith(parmname)]
        if len(matches) > 1:
            raise ValueError(f"Parameter {parmname}* is not unique")
        elif not matches:
            return None, None
        else:
            parmname = matches[0]
            value = localparms.get(parmname, parms.get(parmname))

        if verbose:
            pprint(parmname, value)

    return value


def setparm(parmname: str, value: Any) -> None:
    """Sets a parameter value in the local parameters file."""

    parmfile = Path("parms.mat").absolute()
    if parmfile.exists():
        parms = loadmat(parmfile)
    elif (parmfile.parent.parent / parmfile.name).exists():
        parmfile = parmfile.parent.parent / parmfile.name
        parms = loadmat(parmfile)
    else:
        raise FileNotFoundError(f"`{parmfile}` not found")

    parms[parmname] = value
    sio.savemat(str(parmfile), parms)


def readparm(fname: Path, parm: str, numval: int = 1) -> Any | List[Any]:
    """Reads a parameter value(s) from a file."""

    with fname.open("r") as f:
        lines = f.readlines()

    def convert(val: str) -> Any:
        for conv in (int, float):
            try:
                return conv(val)
            except ValueError:
                pass
        return val

    if numval == 1:
        for line in lines:
            if line.startswith(parm):
                return convert(line.split()[1])
    else:
        values = []
        for line in lines:
            if line.startswith(parm):
                values = [convert(x) for x in line.split()[1:]]
                return values[:numval]


def patchdirs() -> List[Path]:
    """Get the patch directories."""

    dirs = []

    if Path("patch.list").exists():
        # read directory names from patch.list
        with Path("patch.list").open("r") as f:
            dirs = [Path(line.strip()) for line in f.readlines()]
    else:
        # if patch.list does not exist, find all directories with PATCH_ in the name
        dirs = [d for d in Path(".").iterdir() if d.is_dir() and "PATCH_" in d.name]

    if len(dirs) == 0:
        # patch directories not found, use current directory
        dirs.append(Path("."))

    return dirs


def load_initial_gamma(endian: str = "b") -> None:
    """Load all the data we need from GAMMA outputs."""

    phname = Path("./pscands.1.ph")  # phase data
    ijname = Path("./pscands.1.ij")  # pixel location data
    llname = Path("./pscands.1.ll")  # latitude, longitude data
    xyname = Path("./pscands.1.xy")  # local coordinates
    hgtname = Path("./pscands.1.hgt")  # height data
    daname = Path("./pscands.1.da")  # date data
    rscname = Path("../rsc.txt")  # config with master rslc.par file location
    pscname = Path("../pscphase.in")  # config with width and diff phase file locataions

    # Read master day from rsc file
    with rscname.open() as f:
        rslcpar = Path(f.readline().strip())

    log(f"{rslcpar = }")

    # Read interferogram dates
    with pscname.open() as f:
        ifgs = [Path(line.strip()) for line in f.readlines()][1:]

    master_day = rslcpar.name[:8]
    log(f"{master_day = }")

    master_day = datenum(np.array([f"{master_day[0:4]}-{master_day[4:6]}-{master_day[6:8]}"], dtype="datetime64"))[0]
    ifgdts = np.array([f"{ifg.name[9:13]}-{ifg.name[13:15]}-{ifg.name[15:17]}" for ifg in ifgs], dtype="datetime64")
    day = datenum(ifgdts)

    n_image = len(day)
    n_ifg = len(ifgs)

    master_ix = np.sum(day < master_day)
    if day[master_ix] != master_day:
        log(
            f"Master date {rslcpar.name[:8]} == {master_day} not found in interferogram dates, inserting at index {master_ix}"
        )
        day = np.insert(day, master_ix, master_day)

    log(f"{day = }")
    log(f"{master_ix = }")
    log(f"{n_ifg = }")

    # Set and save heading parameter
    heading = readparm(rslcpar, "heading")
    setparm("heading", heading)

    freq = readparm(rslcpar, "radar_frequency")
    lam = 299792458 / freq
    setparm("lambda", lam)

    sensor = readparm(rslcpar, "sensor")
    platform = sensor  # S1 case
    setparm("platform", platform)

    rps = readparm(rslcpar, "range_pixel_spacing")
    rgn = readparm(rslcpar, "near_range_slc")
    se = readparm(rslcpar, "sar_to_earth_center")
    re = readparm(rslcpar, "earth_radius_below_sensor")
    rgc = readparm(rslcpar, "center_range_slc")
    naz = readparm(rslcpar, "azimuth_lines")
    prf = readparm(rslcpar, "prf")

    mean_az = naz / 2 - 0.5

    # Processing of the id, azimuth, range data
    with ijname.open("rb") as f:
        ij = np.loadtxt(f, dtype=np.uint16)

    stamps_save("ij", ij)

    n_ps = len(ij)

    # Processing of the longitude and latitude data
    with llname.open("rb") as f:
        lonlat = np.fromfile(f, dtype=">f4").reshape((-1, 2)).astype(np.float64)

    # Processing of the Height data
    if hgtname.exists():
        with hgtname.open("rb") as f:
            hgt = np.fromfile(f, dtype=np.float32)
    else:
        log(f"{hgtname} does not exist, proceeding without height data.")

    # Calculate range
    rg = rgn + ij[:, 2] * rps

    # Calculate satellite look angles
    look = np.arccos((se**2 + rg**2 - re**2) / (2 * se * rg))

    # Initialize the perpendicular baseline matrix
    bperp_mat = np.zeros((n_ps, n_ifg))
    for i in range(n_ifg):
        basename = ifgs[i].with_suffix(".base")
        B_TCN = readparm(basename, "initial_baseline(TCN)", 3)
        BR_TCN = readparm(basename, "initial_baseline_rate", 3)
        bc = B_TCN[1] + BR_TCN[1] * (ij[:, 1] - mean_az) / prf
        bn = B_TCN[2] + BR_TCN[2] * (ij[:, 1] - mean_az) / prf
        # Convert baselines from (T)CN to perpendicular-parallel coordinates
        bperp_mat[:, i] = bc * np.cos(look) - bn * np.sin(look)
        # log(f"{i = }\n\t{basename = }\n\t{B_TCN = }\n\t{BR_TCN = }\n\t{bc = }\n\t{bn = }")

    # Calculate mean perpendicular baseline
    bperp = np.mean(bperp_mat, axis=0)

    log(f"{bperp = }")

    # Calculate incidence angles
    inci = np.arccos((se**2 - re**2 - rg**2) / (2 * re * rg))

    # Calculate mean incidence angle
    mean_incidence = np.mean(inci)

    # Mean range is given by the center range distance
    mean_range = rgc

    # Processing of the phase data
    with phname.open("rb") as f:
        log(f"Loading phase data from `{phname.absolute()}`")
        ph = np.fromfile(f, dtype=">c8").reshape((n_ifg, n_ps)).T

    mu = np.mean(ph, axis=0)
    for i in range(n_ifg):
        log(f"{ifgs[i]}\tmean(phase) = {mu[i]:+.3f}")
    log(f"{ph.shape = }")

    # Inserting a column of 1's for master image
    ph = np.insert(ph, master_ix, np.ones(n_ps), axis=1)
    bperp = np.insert(bperp, master_ix, 0)
    n_ifg += 1
    n_image += 1

    # Find center longitude and latitude
    ll0 = (np.max(lonlat, axis=0) + np.min(lonlat, axis=0)) / 2

    log(f"{ll0 = }")

    # Convert to local coordinates and scale to meters
    xy = llh2local(lonlat.T, ll0).T * 1000

    # Sort coordinates by x and y
    sort_x = xy[np.argsort(xy[:, 0])]
    sort_y = xy[np.argsort(xy[:, 1])]

    # Determine corners based on a small percentage of points
    n_pc = int(np.round(n_ps * 0.001))
    bl = np.mean(sort_x[:n_pc], axis=0)  # bottom left
    tr = np.mean(sort_x[-n_pc:], axis=0)  # top right
    br = np.mean(sort_y[:n_pc], axis=0)  # bottom right
    tl = np.mean(sort_y[-n_pc:], axis=0)  # top left

    log(f"{bl = }\n{tl = }\n{br = }\n{tr = }")

    # Calculate rotation angle
    theta = (180 - heading) * np.pi / 180
    if theta > np.pi:
        theta -= 2 * np.pi

    log(f"{theta = }")

    # Rotation matrix
    rotm = np.array([[np.cos(theta), np.sin(theta)], [-np.sin(theta), np.cos(theta)]])

    log(f"{rotm = }")

    # Rotate coordinates
    xy = xy.T
    xynew = rotm @ xy

    # Check if rotation improves alignment and apply if it does
    if (np.max(xynew[0]) - np.min(xynew[0]) < np.max(xy[0]) - np.min(xy[0])) and (
        np.max(xynew[1]) - np.min(xynew[1]) < np.max(xy[1]) - np.min(xy[1])
    ):
        log(f"Rotation improved alignment, applying a rotation of {theta * 180 / np.pi:.2f} degrees")
        xy = xynew.T

    # Sort xy in ascending y, then x order and apply sort index to other arrays
    sort_ix = np.lexsort((xy[:, 0], xy[:, 1]))
    stamps_save("sort_ix", sort_ix)

    xy = xy[sort_ix]
    ph = ph[sort_ix]
    lonlat = lonlat[sort_ix]
    bperp_mat = bperp_mat[sort_ix]
    la = inci[sort_ix]

    # Sort and update ij with new point IDs
    ij = ij[sort_ix]
    ij[:, 0] = np.arange(1, n_ps + 1)

    # Round xy to nearest mm
    xy = np.insert(xy, 0, np.arange(1, n_ps + 1), axis=1)
    xy[:, 1:] = np.round(xy[:, 1:] * 1000) / 1000

    with daname.open("rb") as f:
        D_A = np.loadtxt(f)
    D_A = D_A[sort_ix]

    with hgtname.open("rb") as f:
        hgt = np.loadtxt(f, usecols=2)
    hgt = hgt[sort_ix]

    psver = 1

    stamps_save(
        f"ps{psver}",
        ij=ij,
        lonlat=lonlat,
        xy=xy,
        bperp=bperp,
        day=day,
        master_day=master_day,
        master_ix=master_ix,
        n_ifg=n_ifg,
        n_image=n_image,
        n_ps=n_ps,
        sort_ix=sort_ix,
        ll0=ll0,
        mean_incidence=mean_incidence,
        mean_range=mean_range,
    )

    stamps_save(f"ph{psver}", ph)
    stamps_save(f"bp{psver}", bperp_mat)
    stamps_save(f"la{psver}", la)
    stamps_save(f"da{psver}", D_A)
    stamps_save(f"hgt{psver}", hgt)


def clap_filt(ph, alpha=0.5, beta=0.1, n_win=32, n_pad=0, low_pass=None):
    """
    Combined Low-pass Adaptive Phase (CLAP) filtering.
    """

    if low_pass is None:
        low_pass = np.zeros((n_win + n_pad, n_win + n_pad))

    ph_out = np.zeros_like(ph)
    n_i, n_j = ph.shape

    n_inc = n_win // 4
    n_win_i = -(-n_i // n_inc) - 3  # Ceiling division
    n_win_j = -(-n_j // n_inc) - 3

    # Create a window function
    x = np.arange(n_win // 2)
    X, Y = np.meshgrid(x, x)
    wind_func = np.pad(X + Y + 1e-6, ((0, n_win // 2), (0, n_win // 2)), mode="symmetric")

    # Replace NaNs with zeros
    ph = np.nan_to_num(ph)

    # Gaussian smoothing kernel
    B = np.outer(gaussian(7, std=7 / 3), gaussian(7, std=7 / 3))

    ph_bit = np.zeros((n_win + n_pad, n_win + n_pad))

    for ix1 in range(n_win_i):
        i1 = ix1 * n_inc
        i2 = min(i1 + n_win, n_i)
        i1 = max(i2 - n_win, 0)  # Adjust i1 for edge cases
        wf = wind_func[(i2 - i1) - n_win :, : i2 - i1]  # Adjust window function for edges

        for ix2 in range(n_win_j):
            j1 = ix2 * n_inc
            j2 = min(j1 + n_win, n_j)
            j1 = max(j2 - n_win, 0)  # Adjust j1 for edge cases
            wf2 = wf[:, (j2 - j1) - n_win :]  # Adjust window function for edges

            ph_bit[: i2 - i1, : j2 - j1] = ph[i1:i2, j1:j2]
            ph_fft = np.fft.fft2(ph_bit)
            H = np.abs(ph_fft)
            H = fftconvolve(H, B, mode="same")  # Smooth the magnitude response
            meanH = np.median(H)
            H = (H / meanH) ** alpha if meanH != 0 else H**alpha
            H -= 1
            H[H < 0] = 0
            G = H * beta + low_pass[: i2 - i1, : j2 - j1]
            ph_filt = np.fft.ifft2(ph_fft * G).real
            ph_out[i1:i2, j1:j2] += ph_filt[: i2 - i1, : j2 - j1] * wf2

    return ph_out


def goldstein_filt(ph, n_win, alpha, n_pad=None):
    """
    Goldstein's adaptive phase filtering applied to a phase image.
    """

    if n_pad is None:
        n_pad = int(round(n_win * 0.25))

    # Create the Gaussian smoothing kernel
    gauss_kernel = np.outer(gaussian(7, 7 / 3), gaussian(7, 7 / 3))

    # Initialize the output phase array
    ph_out = np.zeros_like(ph)

    # Replace NaNs with zeros
    ph = np.nan_to_num(ph)

    # Compute increments and the number of windows
    n_inc = n_win // 4
    n_win_i, n_win_j = (np.ceil(np.array(ph.shape) / n_inc) - 3).astype(int)

    # Generate the window function using a vectorized approach
    x = np.arange(1, n_win // 2 + 1)
    wind_func = np.pad(x[:, None] + x, ((0, n_win // 2), (0, n_win // 2)), "symmetric")

    def win_bounds(ix, n_inc, n_win, max_bound, wind_func):
        i1 = ix * n_inc
        i2 = min(i1 + n_win, max_bound)
        wf = wind_func[: i2 - i1, : i2 - i1]  # Adjust the window function for window size
        return i1, i2, wf

    # Loop over each window
    for ix1 in range(n_win_i):
        for ix2 in range(n_win_j):
            # Calculate window bounds
            i1, i2, wf_i = win_bounds(ix1, n_inc, n_win, ph.shape[0], wind_func)
            j1, j2, wf_j = win_bounds(ix2, n_inc, n_win, ph.shape[1], wind_func)

            # Extract the current window and apply FFT
            ph_bit = np.zeros((n_win + n_pad, n_win + n_pad))
            ph_bit[: i2 - i1, : j2 - j1] = ph[i1:i2, j1:j2]
            ph_fft = fft2(ph_bit)

            # Apply the adaptive filter in the frequency domain
            H = ifftshift(convolve2d(fftshift(np.abs(ph_fft)), gauss_kernel, mode="same"))
            meanH = np.median(H)
            H = ((H / meanH) ** alpha if meanH != 0 else H**alpha) * (n_win + n_pad) ** 2

            # Inverse FFT and update the output array
            ph_filt = ifft2(ph_fft * H).real[: i2 - i1, : j2 - j1] * (wf_i[:, None] * wf_j)
            ph_out[i1:i2, j1:j2] += ph_filt

    return ph_out


def gradient_filt(ph, n_win):
    """
    Determine 2-D gradient through FFT over windows of size n_win.

    Parameters:
    ph : 2D numpy array, input phase image.
    n_win : int, window size for local FFT.

    Returns:
    ifreq : 2D numpy array, i-component (row-wise) of gradient frequencies.
    jfreq : 2D numpy array, j-component (column-wise) of gradient frequencies.
    ij : 2D numpy array, center coordinates of each window.
    Hmag : 2D numpy array, normalized magnitude of the dominant frequency in each window.
    """
    # Initialize variables
    n_i, n_j = ph.shape
    n_inc = n_win // 4
    n_win_i = -(-n_i // n_inc) - 3  # Ceiling division
    n_win_j = -(-n_j // n_inc) - 3

    # Replace NaNs with zeros
    ph = np.nan_to_num(ph)

    # Initialize output arrays
    Hmag = np.full((n_win_i, n_win_j), np.nan)
    ifreq = Hmag.copy()
    jfreq = Hmag.copy()
    ij = np.full((n_win_i * n_win_j, 2), np.nan)

    def calc_bounds(ix, n_inc, n_win, max_dim):
        i1 = ix * n_inc
        i2 = min(i1 + n_win, max_dim)
        return max(i2 - n_win, 0), i2

    def calc_freq(I1, I2, n_win):
        I1 = (I1 + n_win // 2) % n_win
        I2 = (I2 + n_win // 2) % n_win
        return ((I1 - n_win // 2) * 2 * np.pi / n_win, (I2 - n_win // 2) * -2 * np.pi / n_win)

    idx = 0
    for ix1 in range(n_win_i):
        for ix2 in range(n_win_j):
            # Calculate window bounds
            i1, i2, j1, j2 = calc_bounds(ix1, ix2, n_inc, n_win, n_i, n_j)

            # Extract phase window and apply FFT
            ph_bit = ph[i1:i2, j1:j2]
            if np.count_nonzero(ph_bit) < 6:  # Check for enough non-zero points
                continue

            ph_fft = np.fft.fft2(ph_bit)
            H = np.abs(ph_fft)

            # Find the index of the maximum magnitude
            I = np.argmax(H)
            Hmag_this = H.flat[I] / H.mean()

            # Calculate frequencies
            I1, I2 = np.unravel_index(I, (n_win, n_win))
            ifreq_val, jfreq_val = calc_freq(I1, I2, n_win)

            # Update output arrays
            Hmag[ix1, ix2] = Hmag_this
            ifreq[ix1, ix2] = ifreq_val
            jfreq[ix1, ix2] = jfreq_val
            ij[idx] = [(i1 + i2) / 2, (j1 + j2) / 2]
            idx += 1

    return ifreq.T, jfreq.T, ij, Hmag.T


def topofit(cpxphase, bperp, n_trial_wraps, asym=0):
    """
    Finds the best-fitting range error for complex phase observations.

    Parameters:
    cpxphase : 1D numpy array of complex phase observations.
    bperp : 1D numpy array of perpendicular baseline values corresponding to cpxphase.
    n_trial_wraps : float, the number of trial wraps to consider.
    asym : float, controls the search range for K; -1 for only negative, +1 for only positive, 0 for both.

    Returns:
    K0 : float, estimated range error.
    C0 : float, estimated phase offset.
    coh0 : float, coherence of the fit.
    phase_residual : 1D numpy array, residual phase after removing the fitted model.
    """

    # Ensure cpxphase is a 1D array
    cpxphase = cpxphase.flatten()

    # Filter out zero phase observations
    ix = cpxphase != 0
    cpxphase = cpxphase[ix]
    bperp = bperp[ix]

    # Calculate bperp range
    bperp_range = np.max(bperp) - np.min(bperp)

    # Define trial multipliers for range error search
    trial_mult = (
        np.arange(-int(np.ceil(8 * n_trial_wraps)), int(np.ceil(8 * n_trial_wraps)) + 1) + asym * 8 * n_trial_wraps
    )
    n_trials = len(trial_mult)

    # Calculate trial phase and trial phase matrix for fitting
    trial_phase = bperp / bperp_range * np.pi / 4
    trial_phase_mat = np.exp(-1j * np.outer(trial_phase, trial_mult))

    # Compute phase responses for each trial
    phaser = trial_phase_mat * cpxphase[:, None]
    phaser_sum = np.sum(phaser, axis=0)

    # Calculate trial coherences and offsets
    C_trial = np.angle(phaser_sum)
    coh_trial = np.abs(phaser_sum) / np.sum(np.abs(cpxphase))

    # Find the trial with the highest coherence
    coh_high_max_ix = np.argmax(coh_trial)

    # Estimate range error, phase offset, and coherence
    K0 = np.pi / 4 / bperp_range * trial_mult[coh_high_max_ix]
    C0 = C_trial[coh_high_max_ix]
    coh0 = coh_trial[coh_high_max_ix]

    # Linearize and solve for residual phase
    resphase = cpxphase * np.exp(-1j * (K0 * bperp))
    offset_phase = np.sum(resphase)
    resphase = np.angle(resphase * np.conj(offset_phase))

    # Weighted least squares fit for residual phase
    weighting = np.abs(cpxphase)
    mopt = np.linalg.lstsq(weighting[:, None] * bperp[:, None], weighting * resphase, rcond=None)[0]
    K0 += mopt[0]

    # Calculate phase residuals
    phase_residual = cpxphase * np.exp(-1j * (K0 * bperp))
    mean_phase_residual = np.sum(phase_residual)
    C0 = np.angle(mean_phase_residual)  # Updated static offset
    coh0 = np.abs(mean_phase_residual) / np.sum(np.abs(phase_residual))  # Updated coherence

    return K0, C0, coh0, phase_residual


def estimate_coherence(max_iters=1000) -> None:
    """Estimate the initial coherence (gamma) at the persistent scatterer candidate locations."""

    # Load data
    ps = stamps_load("ps1")
    ph = stamps_load("ph1")
    bp = stamps_load("bp1")
    la = stamps_load("la1")
    da = stamps_load("da1")

    bperp_mat = ps.bperp
    n_ifg = ps.n_ifg
    n_image = ps.n_image
    n_ps = ps.n_ps
    ifgday_ix = ps.ifgday_ix
    xy = ps.xy
    master_ix = ps.master_ix

    #inc_mean = ps.mean_incidence + np.deg2rad(3)
    inc_mean = ps.mean_incidence + 0.052 # StaMPS hardcoded value (approx 3 deg)

    # CLAP filter parameters
    grid_size = getparm("filter_grid_size")
    filter_weighting = getparm("filter_weighting")
    n_win = getparm("clap_win")
    low_pass_wavelength = getparm("clap_low_pass_wavelength")
    clap_alpha = getparm("clap_alpha")
    clap_beta = getparm("clap_beta")

    log(f"{grid_size = }")
    log(f"{filter_weighting = }")
    log(f"{n_win = }")
    log(f"{low_pass_wavelength = }")
    log(f"{clap_alpha = }")
    log(f"{clap_beta = }")

    # For maximum baseline length (max_K) calculation
    max_topo_err = getparm("max_topo_err")
    lambda_ = getparm("lambda")

    log(f"{max_topo_err = }")
    log(f"{lambda_ = }")

    gamma_change_convergence = getparm("gamma_change_convergence")
    gamma_max_iterations = getparm("gamma_max_iterations")
    small_baseline_flag = getparm("small_baseline_flag")

    log(f"{gamma_change_convergence = }")
    log(f"{gamma_max_iterations = }")
    log(f"{small_baseline_flag = }")

    rho = 830000  # mean range - need only be approximately correct
    n_rand = 300000  # number of simulated random phase pixels
    low_coh_thresh = 15  # equivalent to 0.15 in GAMMA

    # Construct a two-dimensional low-pass filter using a Butterworth filter design in the frequency domain
    # used to attenuate high-frequency components in the observations, effectively smoothing them or reducing noise
    freq0 = 1 / low_pass_wavelength
    freq_i = np.arange(-(n_win) / grid_size / n_win / 2, (n_win - 1) / grid_size / n_win / 2, 1 / grid_size / n_win)
    butter_i = 1 / (1 + (freq_i / freq0) ** (2 * 5))
    low_pass = np.outer(butter_i, butter_i)
    low_pass = np.fft.fftshift(low_pass)

    null_i, null_j = np.where(ph == 0)
    null_i = np.unique(null_i)
    good_ix = np.ones(ps.n_ps, dtype=bool)
    good_ix[null_i] = False

    if small_baseline_flag.lower() == "n":
        keep_ix = list(range(master_ix)) + list(range(master_ix + 1, n_ifg))
        ph = ph[:, keep_ix]
        bperp_mat = bperp_mat[keep_ix]
        n_ifg = n_ifg - 1
        n_image = n_image - 1

    # Normalizes the complex phase values to have unit magnitude
    A = np.abs(ph)
    A[A == 0] = 1  # Avoid divide by zero
    ph /= A

    check("nph", ph, atol=1e-6, rtol=1e-6)

    # Calculate the maximum baseline length (max_K) that can be tolerated for a given topographic 
    # error (max_topo_err) considering the wavelength (lambda_) of the radar, the spatial baseline
    # decorrelation coefficient (rho), and the mean incidence angle (inc_mean) of radar signal
    max_K = max_topo_err / (lambda_ * rho * np.sin(inc_mean) / (4 * np.pi))

    bperp_range = np.max(bperp_mat) - np.min(bperp_mat)
    n_trial_wraps = bperp_range * max_K / (2 * np.pi)
    log(f"{n_trial_wraps = }")

    check("ec1", {"max_K": max_K, "max_topo_err": max_topo_err, "lambda": lambda_, "rho": rho, "inc_mean": inc_mean, "bperp_range": bperp_range, "n_trial_wraps": n_trial_wraps})

    if small_baseline_flag.lower() == "y":
        # Generate random phase values for each pixel in each image, scaled by 2*pi
        # This simulates the random phase component in each SAR image
        rand_image = 2 * np.pi * np.random.rand(n_rand, n_image)

        rand_ifg = np.zeros((n_rand, n_ifg))

        for i in range(n_ifg):
            # Calculate the random phase difference for the interferogram
            # This is done by subtracting the random phase of one image from the other,
            # based on the unique day indices for each interferogram (ifgday_ix)
            # This simulates the random phase component in each interferogram
            rand_ifg[:, i] = rand_image[:, ifgday_ix[i, 1]] - rand_image[:, ifgday_ix[i, 0]]
    else:
        # If not using small baseline flag, generate random interferogram phase values directly
        # This simulates random phase components in interferograms without considering individual images
        rand_ifg = 2 * np.pi * np.random.rand(n_rand, n_ifg)

    rand_ifg = loadmat("rand_ifg.mat")['rand_ifg']
    check("rand_ifg", rand_ifg)

    # Pre-compute complex exponential of random interferograms to avoid recalculating in each iteration
    exp_rand_ifg = np.exp(1j * rand_ifg)

    # Iterate through random phase points in reverse order
    coh_rand = np.zeros(n_rand)
    for i in reversed(range(n_rand)):
        # Fit a topographic phase model to each random phase point
        K_r, C_r, coh_r, res_r = topofit(exp_rand_ifg[i, :], bperp_mat, n_trial_wraps)
        # Store the first coherence value for each random point
        coh_rand[i] = coh_r

    check("coh_rand", coh_rand)

    coh_bins = np.arange(0.005, 0.995, 0.01)
    Nr, _ = np.histogram(coh_rand, bins=coh_bins)

    # Find the last non-zero bin index using np.max and np.nonzero
    Nr_max_nz_ix = np.max(np.nonzero(Nr)[0])

    K_ps, C_ps, coh_ps, coh_ps_save, N_opt = (np.zeros(n_ps) for _ in range(5))
    ph_res = np.zeros((n_ps, n_ifg), dtype=np.float32)
    ph_patch = np.zeros_like(ph, dtype=np.float32)
    N_patch = np.zeros(n_ps)

    # Calculate grid indices for the third column of 'xy'
    grid_ij = np.zeros((xy.shape[0], 2), dtype=int)
    grid_ij[:, 0] = np.ceil((xy[:, 2] - np.min(xy[:, 2]) + 1e-6) / grid_size).astype(int)
    # Adjust indices to ensure they are within bounds for the first column
    grid_ij[grid_ij[:, 0] == np.max(grid_ij[:, 0]), 0] = np.max(grid_ij[:, 0]) - 1
    # Calculate grid indices for the second column of 'xy'
    grid_ij[:, 1] = np.ceil((xy[:, 1] - np.min(xy[:, 1]) + 1e-6) / grid_size).astype(int)
    # Adjust indices to ensure they are within bounds for the second column
    grid_ij[grid_ij[:, 1] == np.max(grid_ij[:, 1]), 1] = np.max(grid_ij[:, 1]) - 1

    grid_ij -= 1  # Adjust indices to start from 0

    n_i = np.max(grid_ij[:,0]) + 1
    n_j = np.max(grid_ij[:,1]) + 1

    check("grid_ij", grid_ij, is_idx=True)

    i_loop = 1  # Initialize loop counter
    weighting = 1.0 / da
    weighting_save = weighting.copy()
    gamma_change_save = 0

    for i in range(max_iters):
        log(f"Iteration #{i}, calculating patch phases...")

        # Initialize phase grids for raw phases, filtered phases, and weighted phases
        ph_grid = np.zeros((n_i, n_j, n_ifg), dtype=np.complex128)
        ph_filt = np.copy(ph_grid)  # Copy ph_grid structure for filtered phases

        # Calculate weighted phases, adjusting for baseline and applying weights
        ph_weight = (ph * np.exp(-1j * bperp_mat * K_ps[:, None]) * weighting[:, None])

        # Accumulate weighted phases into grid cells
        for i in range(n_ps):
            ph_grid[grid_ij[i, 0], grid_ij[i, 1], :] = ph_grid[grid_ij[i, 0], grid_ij[i, 1], :] + ph_weight[i, :]

        check("ph_grid", ph_grid, exit=True)

        # Apply filtering to each interferogram in the grid
        for i in range(n_ifg):
            # Apply a CLAP filter (an edge-preserving smoothing filter) to the phase grid
            ph_filt[:, :, i] = clap_filt(
                ph_grid[:, :, i], clap_alpha, clap_beta, int(n_win * 0.75), int(n_win * 0.25), low_pass
            )

        # Extract filtered patch phases for each point
        for i in range(n_ps):
            ph_patch[i, :n_ifg] = ph_filt[grid_ij[i, 0], grid_ij[i, 1], :]

        # Clear the filtered phase grid to free memory
        del ph_filt

        # Normalize non-zero phase patch values to unit magnitude
        ix = ph_patch != 0
        ph_patch[ix] = ph_patch[ix] / np.abs(ph_patch[ix])

        log("Estimating topo error...")

        K_ps = np.full(n_ps, np.nan)
        C_ps = np.zeros(n_ps)
        coh_ps = np.zeros(n_ps)
        N_opt = np.zeros(n_ps, dtype=int)
        ph_res = np.zeros((n_ps, n_ifg))

        for i in range(n_ps):
            # Calculate phase difference between observed and filtered phase
            psdph = ph[i, :] * np.conj(ph_patch[i, :])

            # Check if there's a non-null value in every interferogram
            if np.all(psdph != 0):
                # Fit the topographic phase model to the phase difference
                Kopt, Copt, cohopt, ph_residual = topofit(psdph, bperp_mat[i, :].reshape(-1, 1), n_trial_wraps)

                # Store the results
                K_ps[i] = Kopt[0]
                C_ps[i] = Copt[0]
                coh_ps[i] = cohopt[0]
                N_opt[i] = len(Kopt)
                ph_res[i, :] = np.angle(ph_residual)
            else:
                # Assign default values in case of null values
                K_ps[i] = np.nan
                coh_ps[i] = 0

            # Log progress for every 100000 points processed
            if i % 100000 == 0 and i > 0:
                log(f"{i} PS processed")

        # Replace NaNs in coherence with zeros
        coh_ps[np.isnan(coh_ps)] = 0

        # Calculate the RMS change in coherence values, ignoring NaNs
        gamma_change_rms = np.sqrt(np.nanmean((coh_ps - coh_ps_save) ** 2))

        # Calculate the change in gamma_change_rms from the previous iteration
        gamma_change_change = gamma_change_rms - gamma_change_save

        # Log the change in gamma_change_rms
        log(f"{gamma_change_change =:.6f}")

        # Save the current values for comparison in the next iteration
        gamma_change_save = gamma_change_rms
        coh_ps_save = coh_ps.copy()

    stamps_save("pm1",
                ph_patch=ph_patch,
                K_ps=K_ps,
                C_ps=C_ps,
                coh_ps=coh_ps,
                N_opt=N_opt,
                ph_res=ph_res,
                # step_number=step_number,
                ph_grid=ph_grid,
                n_trial_wraps=n_trial_wraps,
                grid_ij=grid_ij,
                grid_size=grid_size,
                low_pass=low_pass,
                i_loop=i_loop,
                # ph_weight=ph_weight,
                #Nr=Nr,
                #Nr_max_nz_ix=Nr_max_nz_ix,
                #coh_bins=coh_bins,
                #coh_ps_save=coh_ps_save,
                #gamma_change_save=gamma_change_save,
                )


def results_equal(name: str, is_idx=False, atol=1e-8, rtol=1e-8, equal_nan=True) -> bool:
    """Check if the results of the current run match the expected results."""

    python = stamps_load(name)
    matlab = loadmat(f"{name}.mat")

    if name.endswith("1"):
        name = name[:-1]

    ndiff = 0

    def compare_array(x, y) -> bool:
        print(f"{x.dtype=} vs {y.dtype=}")
        print(f"{x.shape=} vs {y.shape=}")
        print(f"Python:\n{x}")
        print(f"MATLAB:\n{y}")
        diff = np.abs(x - y)
        print(f"Count of differences: {np.sum(diff > 1e-5)}")
        ix = np.unravel_index(np.argmax(diff), diff.shape)
        print(f"Location of max difference: {ix}")
        print(f"Python value: {x[ix]}")
        print(f"MATLAB value: {y[ix]}")
        if x.shape == y.shape:
            # print out values from equivalent columns next to each other
            nprinted = 0
            for i in range(x.shape[0]):
                if np.allclose(x[i], y[i], rtol=rtol, atol=atol, equal_nan=equal_nan):
                    continue
                nprinted += 1
                print(f"Row {i}:\t{x[i]}\t{y[i]}")
                if nprinted > 10:
                    break
        print(f"Location of max difference: {ix}")
        print("")

    if isinstance(python, np.ndarray):
        if isinstance(matlab, dict):
            try:
                if len(matlab.keys()) == 1:
                    matlab = matlab[list(matlab.keys())[0]]
                else:
                    matlab = matlab[name]
            except KeyError:
                print(f"Unknown key `{name}`, debugging...")
                breakpoint()
        if is_idx:
            matlab = matlab - 1
        if not np.allclose(python, matlab, rtol=rtol, atol=atol, equal_nan=equal_nan):
            print(f"\nError: `{name}` does not match")
            compare_array(python, matlab)
            return False
        else:
            print(f"`{name}` matches. {python.dtype=} {matlab.dtype=}. {atol=}, {rtol=}")
            return True

    for key in python:
        if "ix" in key:
            log(f"`{key}` check skipped")
            continue
        if key in matlab:
            if isinstance(python[key], np.ndarray):
                if not np.allclose(python[key], matlab[key], rtol=rtol, atol=atol, equal_nan=equal_nan):
                    print(f"\nError: `{key}` does not match")
                    compare_array(python[key], matlab[key])
                    ndiff += 1
                else:
                    print(f"`{key}` matches with {atol = } and {rtol = }")
            else:
                if abs(python[key] - matlab[key]) > atol:
                    print(f"\nError: `{key}` does not match")
                    print(f"Python: {python[key]}")
                    print(f"MATLAB: {matlab[key]}")
                    print("")
                    ndiff += 1
                else:
                    print(f"`{key}` matches with {atol = } and {rtol = }")

    if ndiff > 0:
        breakpoint()

    return ndiff == 0


def check(name, x, is_idx=False, atol=1e-6, rtol=1e-6, exit=False) -> None:
    if isinstance(x, dict):
        stamps_save(name, **x)
    else:
        stamps_save(name, x)
    assert results_equal(name, is_idx=is_idx, atol=atol, rtol=rtol)
    if exit:
        sys.exit(0)

def test_getparm() -> None:
    """Test the getparm function."""
    getparm()


def test_load_initial_gamma() -> None:
    cwd = Path.cwd()
    new = patchdirs()[0]
    os.chdir(new)
    load_initial_gamma()
    assert results_equal("ps1")
    assert results_equal("ph1")
    assert results_equal("bp1")
    assert results_equal("la1")
    assert results_equal("da1")
    assert results_equal("hgt1")
    os.chdir(cwd)


def test_estimate_coherence() -> None:
    cwd = Path.cwd()
    new = patchdirs()[0]
    os.chdir(new)
    estimate_coherence()
    os.chdir(cwd)


if __name__ == "__main__":
    #test_load_initial_gamma()
    test_estimate_coherence()
