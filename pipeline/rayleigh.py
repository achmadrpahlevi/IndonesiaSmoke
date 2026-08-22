"""First-order Rayleigh path-reflectance correction.

Why this exists: mask thresholds drift badly with sun angle. Rayleigh optical
depth goes as roughly lambda^-4, so it is large at 0.47 um, modest at 0.64 um
and negligible in the SWIR. The B03-B06 contrast the smoke test relies on
therefore gains a purely atmospheric term as the sun drops — and because
Rayleigh path reflectance itself scales as 1/cos(SZA), dividing by cos(SZA)
for the solar-zenith correction amplifies it a second time.

Single-scattering approximation, which is the right level of effort here: it
captures the 1/(cos_s cos_v) geometry that causes the drift without pulling in
a radiative transfer model.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np

from . import config as C

R_EARTH_KM = 6371.0
SAT_ALT_KM = 35786.0

# Rayleigh optical depth at each band centre, from
#   tau = 0.008569 l^-4 (1 + 0.0113 l^-2 + 0.00013 l^-4),  l in um
BAND_WAVELENGTH_UM = {
    "B01": 0.47, "B02": 0.51, "B03": 0.64, "B04": 0.86,
    "B05": 1.61, "B06": 2.26,
}


def rayleigh_optical_depth(wavelength_um: float) -> float:
    l = wavelength_um
    return 0.008569 * l**-4 * (1 + 0.0113 * l**-2 + 0.00013 * l**-4)


TAU_R = {b: rayleigh_optical_depth(w) for b, w in BAND_WAVELENGTH_UM.items()}


def view_zenith(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Satellite zenith angle, degrees, for a geostationary sensor."""
    psi = np.arccos(
        np.clip(
            np.cos(np.radians(lat)) * np.cos(np.radians(lon - C.AHI_SATELLITE_LON)),
            -1.0, 1.0,
        )
    )
    rs = R_EARTH_KM + SAT_ALT_KM
    d = np.sqrt(R_EARTH_KM**2 + rs**2 - 2 * R_EARTH_KM * rs * np.cos(psi))
    return np.degrees(np.arcsin(np.clip(rs * np.sin(psi) / d, -1.0, 1.0)))


def view_azimuth(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Bearing from each pixel to the sub-satellite point, degrees from north."""
    dlon = np.radians(C.AHI_SATELLITE_LON - lon)
    latr = np.radians(lat)
    y = np.sin(dlon)
    x = np.cos(latr) * np.tan(0.0) - np.sin(latr) * np.cos(dlon)
    return (np.degrees(np.arctan2(y, x)) + 360.0) % 360.0


def solar_azimuth(dt: datetime, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Solar azimuth, degrees from north, same NOAA formulation as the elevation."""
    dt = dt.astimezone(timezone.utc)
    doy = dt.timetuple().tm_yday
    hour = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    gamma = 2.0 * math.pi / 365.0 * (doy - 1 + (hour - 12) / 24.0)
    eqtime = 229.18 * (
        0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma)
    )
    decl = (
        0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma)
    )
    latr = np.radians(lat)
    tst = hour * 60.0 + eqtime + 4.0 * lon
    ha = np.radians(tst / 4.0 - 180.0)
    cos_z = np.sin(latr) * math.sin(decl) + np.cos(latr) * math.cos(decl) * np.cos(ha)
    cos_z = np.clip(cos_z, -1.0, 1.0)
    zen = np.arccos(cos_z)
    denom = np.where(np.abs(np.sin(zen)) < 1e-6, 1e-6, np.sin(zen))
    cos_az = (math.sin(decl) - np.sin(latr) * cos_z) / (np.cos(latr) * denom)
    az = np.degrees(np.arccos(np.clip(cos_az, -1.0, 1.0)))
    return np.where(ha > 0, 360.0 - az, az)


def path_reflectance(band: str, sza: np.ndarray, vza: np.ndarray,
                     raa: np.ndarray) -> np.ndarray:
    """Single-scattering Rayleigh reflectance, in PERCENT to match the bands."""
    tau = TAU_R.get(band)
    if tau is None:
        return np.zeros_like(sza, dtype=np.float32)
    mu_s = np.clip(np.cos(np.radians(sza)), C.MIN_COS_SZA, 1.0)
    mu_v = np.clip(np.cos(np.radians(vza)), 0.1, 1.0)
    # Scattering angle: 180 deg is pure backscatter.
    cos_theta = -mu_s * mu_v + np.sqrt(1 - mu_s**2) * np.sqrt(1 - mu_v**2) * np.cos(
        np.radians(raa)
    )
    phase = 0.75 * (1.0 + cos_theta**2)
    return (100.0 * tau * phase / (4.0 * mu_s * mu_v)).astype(np.float32)
