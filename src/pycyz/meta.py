"""
Typed accessors for measurement results and sensor logs.

Both live inside the ``DataFileInfo`` NRBF segment already parsed into
``CyzFileData.datafile_info``.  This module extracts them into proper
dataclasses so callers don't have to spelunk the raw dict.

Reference: CyzFile-API Data.vb (MeasurementInfo, SensorDataPointLogs,
DataPointList structures).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .cyz import _find_nested, _get_field, dotnet_ticks_to_datetime


# ── Sensor time-series ─────────────────────────────────────────────────

@dataclass(frozen=True)
class SensorReading:
    """A single timestamped sensor measurement."""

    time: datetime
    value: float


def _parse_data_point_list(obj: Any) -> list[SensorReading]:
    """Parse a ``DataPointList`` NRBF dict into a list of :class:`SensorReading`.

    ``DataPointList`` stores parallel arrays:
    * ``data()``  – ``Double()`` values
    * ``d()``     – ``DateTime()`` ticks (Int64)
    * ``count``   – number of valid entries
    """
    if not isinstance(obj, dict):
        return []

    data_arr = _get_field(obj, "data", [])
    time_arr = _get_field(obj, "d", [])
    count = _get_field(obj, "count", None)

    if not isinstance(data_arr, (list, bytes)):
        return []
    if not isinstance(time_arr, list):
        return []

    n = min(len(data_arr), len(time_arr))
    if count is not None and isinstance(count, int):
        n = min(n, count)

    readings: list[SensorReading] = []
    for i in range(n):
        val = data_arr[i]
        tick = time_arr[i]
        if not isinstance(tick, int):
            continue
        readings.append(SensorReading(
            time=dotnet_ticks_to_datetime(tick),
            value=float(val),
        ))
    return readings


@dataclass
class SensorLogs:
    """Time-series sensor readings recorded during a measurement.

    Each attribute is a (possibly empty) list of :class:`SensorReading`
    objects sorted by the order they were recorded.

    All field names match the VB.NET ``SensorDataPointLogs`` Structure
    members exactly (no underscore prefix — they are ``Dim`` fields in a
    VB Structure).

    Attributes
    ----------
    system_temp:
        System enclosure temperature (°C).
    sheath_temp:
        Sheath fluid temperature (°C).
    pmt_temp:
        Photomultiplier tube temperature (°C).
    buoy_temp:
        Buoy / float sensor temperature (°C).
    laser_temp:
        Laser body temperature (°C).
    internal_temp:
        Internal temperature (°C).
    laser1_diode_temp:
        Laser-1 diode temperature (°C).
    laser1_base_temp:
        Laser-1 base / TEC temperature (°C).
    pressure_abs:
        Internal absolute pressure (mbar) — ``intPressure_absolute``.
    pressure_diff:
        Internal differential pressure (mbar) — ``intPressure_differential``.
    pic_pressure_abs:
        PIC-measured absolute pressure (mbar).
    pic_pressure_diff:
        PIC-measured differential pressure (mbar).
    ext_pressure:
        External pressure (mbar or bar depending on sensor).
    sheath_flow:
        Sheath flow rate.
    pic_concentration:
        Real-time particle concentration (n/µL) from PIC counter.
    pic_pre_concentration:
        Pre-measurement concentration baseline (n/µL).
    laser1_diode_current:
        Laser-1 diode current (mA).
    laser1_tec_load:
        Laser-1 TEC load (%).
    laser1_input_voltage:
        Laser-1 input voltage (V).
    """

    # Temperature
    system_temp: list[SensorReading] = field(default_factory=list)
    sheath_temp: list[SensorReading] = field(default_factory=list)
    pmt_temp: list[SensorReading] = field(default_factory=list)
    buoy_temp: list[SensorReading] = field(default_factory=list)
    laser_temp: list[SensorReading] = field(default_factory=list)
    internal_temp: list[SensorReading] = field(default_factory=list)
    laser1_diode_temp: list[SensorReading] = field(default_factory=list)
    laser1_base_temp: list[SensorReading] = field(default_factory=list)

    # Pressure
    pressure_abs: list[SensorReading] = field(default_factory=list)
    pressure_diff: list[SensorReading] = field(default_factory=list)
    pic_pressure_abs: list[SensorReading] = field(default_factory=list)
    pic_pressure_diff: list[SensorReading] = field(default_factory=list)
    ext_pressure: list[SensorReading] = field(default_factory=list)

    # Flow & concentration
    sheath_flow: list[SensorReading] = field(default_factory=list)
    pic_concentration: list[SensorReading] = field(default_factory=list)
    pic_pre_concentration: list[SensorReading] = field(default_factory=list)

    # Laser
    laser1_diode_current: list[SensorReading] = field(default_factory=list)
    laser1_tec_load: list[SensorReading] = field(default_factory=list)
    laser1_input_voltage: list[SensorReading] = field(default_factory=list)

    def available(self) -> list[str]:
        """Return names of sensors that have at least one reading."""
        return [
            name for name, val in self.__dict__.items()
            if isinstance(val, list) and len(val) > 0
        ]


# Mapping: SensorLogs attribute → SensorDataPointLogs field name in the NRBF dict
_SENSOR_MAP: list[tuple[str, str]] = [
    ("system_temp",          "SystemTemp"),
    ("sheath_temp",          "SheathTemp"),
    ("pmt_temp",             "PMTTemp"),
    ("buoy_temp",            "BuoyTemp"),
    ("laser_temp",           "LaserTemp"),
    ("internal_temp",        "internalTemperature"),
    ("laser1_diode_temp",    "Laser1DiodeTemperature"),
    ("laser1_base_temp",     "Laser1BaseTemperature"),
    ("pressure_abs",         "intPressure_absolute"),
    ("pressure_diff",        "intPressure_differential"),
    ("pic_pressure_abs",     "PICintPressure_absolute"),
    ("pic_pressure_diff",    "PICintPressure_differential"),
    ("ext_pressure",         "extPressure"),
    ("sheath_flow",          "SheathFlow"),
    ("pic_concentration",    "PICConcentration"),
    ("pic_pre_concentration","PICPreConcentration"),
    ("laser1_diode_current", "Laser1DiodeCurrent"),
    ("laser1_tec_load",      "Laser1TecLoad"),
    ("laser1_input_voltage", "Laser1InputVoltage"),
]


def parse_sensor_logs(measurement_info: dict) -> SensorLogs:
    """Extract sensor time-series from a deserialized ``MeasurementInfo`` dict.

    Parameters
    ----------
    measurement_info:
        The dict returned by ``CyzFileData.measurement_info``.
    """
    logs = SensorLogs()

    sensor_dict = _get_field(measurement_info, "sensorLogs")
    if not isinstance(sensor_dict, dict):
        return logs

    for attr, net_name in _SENSOR_MAP:
        raw = _get_field(sensor_dict, net_name)
        if raw is not None:
            object.__setattr__(logs, attr, _parse_data_point_list(raw)) \
                if False else setattr(logs, attr, _parse_data_point_list(raw))

    return logs


# ── Measurement results ────────────────────────────────────────────────

@dataclass
class MeasurementResults:
    """Summary results from one CytoSense measurement run.

    Attributes
    ----------
    duration_s:
        Actual measurement duration in seconds (``MeasureTime``).
    counted_particles:
        Particles detected by the hardware trigger
        (``NumberofParticles``).
    picture_count:
        Number of IIF images taken (``_numberOfPictures``).
    smart_triggered_particles:
        Particles captured by the smart-trigger sub-system
        (``NumberofParticles_smartTriggered``), if present.
    """

    duration_s: float | None = None
    counted_particles: int | None = None
    picture_count: int | None = None
    smart_triggered_particles: int | None = None


def parse_measurement_results(measurement_info: dict) -> MeasurementResults:
    """Extract scalar measurement results from a deserialized
    ``MeasurementInfo`` dict.

    Parameters
    ----------
    measurement_info:
        The dict returned by ``CyzFileData.measurement_info``.
    """

    def _int_or_none(v: Any) -> int | None:
        return int(v) if isinstance(v, (int, float)) else None

    def _float_or_none(v: Any) -> float | None:
        return float(v) if isinstance(v, (int, float)) else None

    duration = _float_or_none(
        _get_field(measurement_info, "MeasureTime")
        or _get_field(measurement_info, "ActualMeasureTime")
    )
    counted = _int_or_none(_get_field(measurement_info, "NumberofParticles"))
    pictures = _int_or_none(_get_field(measurement_info, "_numberOfPictures"))
    smart = _int_or_none(
        _get_field(measurement_info, "NumberofParticles_smartTriggered")
    )

    return MeasurementResults(
        duration_s=duration,
        counted_particles=counted,
        picture_count=pictures,
        smart_triggered_particles=smart,
    )
