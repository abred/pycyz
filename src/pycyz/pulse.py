"""
Pulse parameter computation from raw ADC channel data.

Formulas match those in CyzFile-API (ChannelData.vb) exactly:
  - Raw samples are unsigned 8-bit integers (one byte per ADC step).
  - Most parameters (CoG, inertia, asymmetry, average) operate on
    data[0 : n-1] (excluding the last sample) to match legacy behaviour.
  - Fill factor and cell count use all n samples.

Reference: https://github.com/Cytobuoy/CyzFile-API
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class PulseParams:
    """Derived parameters computed from a single-channel ADC pulse.

    All parameters are computed from the raw uint8 sample array stored in
    ``Particle.channel_data[i]``.  Pass that bytes object directly to
    :func:`compute_params`.

    Attributes
    ----------
    length:
        Number of ADC samples in the pulse (i.e. ``len(data)``).
    total:
        Sum of all sample values.  Proportional to total scattered / emitted
        light energy.
    maximum:
        Peak sample value.
    average:
        Mean of ``data[:-1]`` (excludes last sample — legacy CyzFile-API
        behaviour).
    fill_factor:
        ``total² / (length * Σ(data[i]²))``.  Equals 1 for a perfect
        rectangular pulse; lower values indicate a more peaked pulse.
    asymmetry:
        ``|2 * cog / (length - 1) - 1|``.  0 = perfectly centred peak;
        approaches 1 as the peak shifts to either end.
    centre_of_gravity:
        Weighted centroid of ``data[:-1]``:
        ``Σ(i * data[i]) / Σ(data[i])`` for *i* in 0 … n-2.
    inertia:
        Normalised second moment of ``data[:-1]`` about the centre of
        gravity.  Normalised by the moment of a uniform distribution of the
        same total so that a flat-top pulse gives inertia ≈ 1.
    cell_count:
        Estimate of the number of distinct cells in the pulse, derived from
        the spectral curvature: ``(n / 2π) * √(Σ Δdata² / variance)``.
        Returns ``math.nan`` when the pulse is constant (zero variance).
    """

    length: int
    total: float
    maximum: float
    average: float
    fill_factor: float
    asymmetry: float
    centre_of_gravity: float
    inertia: float
    cell_count: float


def compute_params(data: bytes) -> PulseParams:
    """Compute standard CytoSense pulse parameters from raw ADC bytes.

    Parameters
    ----------
    data:
        Raw pulse samples for one channel as returned by
        ``Particle.channel_data[i]``.  Each byte is one unsigned 8-bit ADC
        sample.

    Returns
    -------
    PulseParams
        All computed parameters.  If ``data`` is empty, all numeric fields
        are 0 and ``cell_count`` is ``math.nan``.
    """
    n = len(data)
    if n == 0:
        return PulseParams(
            length=0, total=0.0, maximum=0.0, average=0.0,
            fill_factor=0.0, asymmetry=0.0,
            centre_of_gravity=0.0, inertia=0.0, cell_count=math.nan,
        )

    # ── All-sample quantities ─────────────────────────────────────────
    total = float(sum(data))
    maximum = float(max(data))

    sum_sq = sum(v * v for v in data)
    fill_factor = (total * total) / (n * sum_sq) if sum_sq > 0 else 0.0

    # ── data[:-1] quantities (legacy: exclude last sample) ────────────
    # When n == 1 the "excl-last" slice is empty; guard against that.
    excl = data[:-1]      # memoryview-like slice — still bytes, O(1) copy
    m = len(excl)         # = n - 1

    if m == 0:
        # Single-sample degenerate pulse
        average = float(data[0])
        cog = 0.0
        inertia = 0.0
        asymmetry = 0.0
    else:
        total_excl = float(sum(excl))
        average = total_excl / m

        # Centre of gravity: Σ(i * data[i]) / Σ(data[i])
        if total_excl > 0:
            cog = sum(i * v for i, v in enumerate(excl)) / total_excl
        else:
            cog = 0.0

        # Inertia: |Σ(i² * data[i]) - CoG² * total_excl| / M_normal
        # M_normal = total_excl * (n-1)² / 12
        sum_i2 = sum(i * i * v for i, v in enumerate(excl))
        m_normal = total_excl * (m * m) / 12.0
        if m_normal > 0:
            inertia = abs(sum_i2 - cog * cog * total_excl) / m_normal
        else:
            inertia = 0.0

        # Asymmetry: |2 * CoG / (n-1) - 1|
        asymmetry = abs(2.0 * cog / m - 1.0)

    # ── Cell count ────────────────────────────────────────────────────
    # numerator  = Σ (data[i] - data[i-1])²  for i in 1..n-1
    # denominator = Σ data[i]² - total²/n
    numerator = sum((data[i] - data[i - 1]) ** 2 for i in range(1, n))
    denominator = sum_sq - total * total / n
    if denominator > 0:
        cell_count = (n / (2.0 * math.pi)) * math.sqrt(numerator / denominator)
    else:
        cell_count = math.nan

    return PulseParams(
        length=n,
        total=total,
        maximum=maximum,
        average=average,
        fill_factor=fill_factor,
        asymmetry=asymmetry,
        centre_of_gravity=cog,
        inertia=inertia,
        cell_count=cell_count,
    )
