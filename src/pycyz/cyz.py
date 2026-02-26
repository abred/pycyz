"""
CYZ domain layer: data structures and file reader.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, BinaryIO

from .nrbf import NRBFReader
from .pulse import PulseParams, compute_params

# .NET DateTime epoch
_DOTNET_EPOCH = datetime(1, 1, 1)
_TICKS_PER_MICROSECOND = 10  # 1 tick = 100 nanoseconds
_TICKS_MASK = 0x3FFFFFFFFFFFFFFF  # top 2 bits encode DateTimeKind

# DataSegmentType enum values
SEG_HEADER = 0
SEG_DATAFILE_INFO = 1
SEG_ALL_PARTICLE_DATA = 2
SEG_SMART_TRIGGER_DATA = 3
SEG_IIF_PARTICLE_DATA = 4

SEG_TYPE_NAMES = {
    0: "Header",
    1: "DataFileInfo",
    2: "AllParticleData",
    3: "SmartTriggerData",
    4: "IIFParticleData",
}


def dotnet_ticks_to_datetime(ticks: int) -> datetime:
    """Convert a .NET DateTime ticks value to a Python :class:`datetime`."""
    ticks = ticks & _TICKS_MASK
    microseconds = ticks // _TICKS_PER_MICROSECOND
    return _DOTNET_EPOCH + timedelta(microseconds=microseconds)


def _get_field(d: dict, field_name: str, default: Any = None) -> Any:
    """Look up a field in a deserialized .NET object dict.

    .NET BinaryFormatter prefixes inherited fields with ``'ClassName+'``.
    This helper checks both the plain name and any prefixed variant.
    """
    if field_name in d:
        return d[field_name]
    suffix = "+" + field_name
    for k, v in d.items():
        if k.endswith(suffix):
            return v
    return default


def _find_nested(d: Any, *keys: str) -> Any:
    """Return the first matching value for any key in a nested dict."""
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d:
            return d[k]
    for v in d.values():
        if isinstance(v, dict):
            result = _find_nested(v, *keys)
            if result is not None:
                return result
    return None


@dataclass
class SegmentInfo:
    offset: int
    count: int
    segment_type: int

    @staticmethod
    def from_dict(d: dict) -> SegmentInfo:
        stype_val = d.get("_type", 0)
        if isinstance(stype_val, dict):
            stype_val = stype_val.get("value__", 0)
        return SegmentInfo(
            offset=d["_offset"],
            count=d["_count"],
            segment_type=stype_val,
        )


@dataclass
class Particle:
    """A single measured particle with raw channel pulse data."""

    id: int
    time_of_arrival: datetime
    channel_data: list[bytes]

    def channel_params(self, channel_index: int) -> PulseParams:
        """Compute pulse parameters for one channel.

        Parameters
        ----------
        channel_index:
            Index into :attr:`channel_data`.

        Returns
        -------
        PulseParams
            All derived parameters (total, max, fill factor, etc.).

        Raises
        ------
        IndexError
            If *channel_index* is out of range.
        """
        return compute_params(self.channel_data[channel_index])

    def all_channel_params(self) -> list[PulseParams]:
        """Return :class:`PulseParams` for every channel in this particle."""
        return [compute_params(ch) for ch in self.channel_data]

    @staticmethod
    def from_dict(d: dict) -> Particle:
        ticks = _get_field(d, "_timeOfArrival", 0)
        chdata = _get_field(d, "_chdata", [])
        channels: list[bytes] = []
        if isinstance(chdata, list):
            for ch in chdata:
                if isinstance(ch, (bytes, bytearray)):
                    channels.append(bytes(ch))
        return Particle(
            id=_get_field(d, "_ID", -1),
            time_of_arrival=dotnet_ticks_to_datetime(ticks),
            channel_data=channels,
        )


@dataclass
class ImagedParticle(Particle):
    """A particle that also carries an IIF camera image."""

    image_data: bytes = b""
    crop_rect: tuple[int, int, int, int] = (0, 0, 0, 0)

    @staticmethod
    def from_dict(d: dict) -> ImagedParticle:
        base = Particle.from_dict(d)
        image_data = b""
        crop_rect = (0, 0, 0, 0)

        img_stream = _get_field(d, "_ImageStream")
        if isinstance(img_stream, dict):
            buf = img_stream.get("_buffer", b"")
            length = img_stream.get("_length", 0)
            if isinstance(buf, (bytes, bytearray)) and length > 0:
                image_data = bytes(buf[:length])

        rect = _get_field(d, "_CropRect") or _get_field(d, "_cropRect")
        if isinstance(rect, dict):
            crop_rect = (
                rect.get("X", 0),
                rect.get("Y", 0),
                rect.get("Width", 0),
                rect.get("Height", 0),
            )

        return ImagedParticle(
            id=base.id,
            time_of_arrival=base.time_of_arrival,
            channel_data=base.channel_data,
            image_data=image_data,
            crop_rect=crop_rect,
        )


@dataclass
class CyzFileData:
    """Parsed contents of a .cyz file."""

    segments: list[SegmentInfo] = field(default_factory=list)
    df_info_segment: SegmentInfo | None = None
    datafile_info: dict = field(default_factory=dict)
    particles: list[Particle] = field(default_factory=list)
    imaged_particles: list[ImagedParticle] = field(default_factory=list)

    @property
    def measurement_info(self) -> dict | None:
        return _find_nested(
            self.datafile_info,
            "MeasurementInfo", "_measurementInfo", "measurementInfo",
        )

    @property
    def cyto_settings(self) -> dict | None:
        return _find_nested(
            self.datafile_info,
            "CytoSenseSetting", "_cytoSenseSetting", "cytoSenseSetting",
        )

    @property
    def measurement_results(self):
        """Scalar measurement results as a :class:`~pycyz.meta.MeasurementResults`.

        Returns ``None`` if the DataFileInfo segment could not be parsed.
        """
        from .meta import MeasurementResults, parse_measurement_results
        mi = self.measurement_info
        if mi is None:
            return None
        return parse_measurement_results(mi)

    @property
    def sensor_logs(self):
        """Sensor time-series as a :class:`~pycyz.meta.SensorLogs`.

        Returns an empty :class:`~pycyz.meta.SensorLogs` if no sensor data is
        present.
        """
        from .meta import SensorLogs, parse_sensor_logs
        mi = self.measurement_info
        if mi is None:
            return SensorLogs()
        return parse_sensor_logs(mi)

    @property
    def gps_data(self) -> list[dict]:
        mi = self.measurement_info
        if not mi:
            return []
        gps = _get_field(mi, "_GPSData") or _get_field(mi, "GPSData")
        if isinstance(gps, dict):
            items = gps.get("_items", [])
            size = gps.get("_size", len(items))
            return [g for g in items[:size] if isinstance(g, dict)]
        if isinstance(gps, list):
            return [g for g in gps if isinstance(g, dict)]
        return []

    @property
    def channel_names(self) -> list[str]:
        cs = self.cyto_settings
        if not cs:
            return []
        channels = _get_field(cs, "channels") or _get_field(cs, "_channels", [])
        if isinstance(channels, dict):
            items = channels.get("_items", [])
            size = channels.get("_size", len(items))
            channels = items[:size]
        if not isinstance(channels, list):
            return []
        names = []
        for ch in channels:
            if isinstance(ch, dict):
                name = (
                    _get_field(ch, "name")
                    or _get_field(ch, "_name")
                    or "?"
                )
                visible = _get_field(ch, "visible", True)
                if visible:
                    names.append(name)
        return names


# ── File reader ────────────────────────────────────────────────────────


def _deserialize_at(f: BinaryIO, offset: int, size: int | None = None) -> Any:
    f.seek(offset)
    reader = NRBFReader(f, limit=size)
    return reader.read()


def _extract_header(header_obj: dict) -> tuple[SegmentInfo, list[SegmentInfo]]:
    df_info_raw = header_obj.get("_df_info", {})
    df_info = SegmentInfo.from_dict(df_info_raw)

    segments_raw = header_obj.get("_segments", {})
    if isinstance(segments_raw, dict):
        items = segments_raw.get("_items", [])
        size = segments_raw.get("_size", len(items))
        items = items[:size]
    elif isinstance(segments_raw, list):
        items = segments_raw
    else:
        items = []

    segments = [SegmentInfo.from_dict(s) for s in items if isinstance(s, dict)]
    return df_info, segments


def _extract_particles(segment_obj: dict) -> list[Particle]:
    particles_raw = segment_obj.get("_particles", {})
    if isinstance(particles_raw, dict):
        items = particles_raw.get("_items", [])
        size = particles_raw.get("_size", len(items))
        items = items[:size]
    elif isinstance(particles_raw, list):
        items = particles_raw
    else:
        items = []
    return [Particle.from_dict(p) for p in items if isinstance(p, dict)]


def _extract_iif_particles(segment_obj: dict) -> list[ImagedParticle]:
    particles_raw = segment_obj.get("_particles", {})
    if isinstance(particles_raw, dict):
        items = particles_raw.get("_items", [])
        size = particles_raw.get("_size", len(items))
        items = items[:size]
    elif isinstance(particles_raw, list):
        items = particles_raw
    else:
        items = []
    return [ImagedParticle.from_dict(p) for p in items if isinstance(p, dict)]


def read_cyz(path: str, skip_particles: bool = False) -> CyzFileData:
    """Read a ``.cyz`` file and return a :class:`CyzFileData` instance.

    Parameters
    ----------
    path:
        Path to the ``.cyz`` file.
    skip_particles:
        If ``True``, only the header and metadata segments are parsed.
        Useful for quickly inspecting file info without loading all particle
        data into memory.
    """
    result = CyzFileData()

    with open(path, "rb") as f:
        header_obj = _deserialize_at(f, 0)
        if not isinstance(header_obj, dict):
            raise ValueError("Failed to parse CYZ header")

        df_info, segments = _extract_header(header_obj)
        result.df_info_segment = df_info
        result.segments = segments

        try:
            datafile_obj = _deserialize_at(f, df_info.offset, df_info.count)
            if isinstance(datafile_obj, dict):
                result.datafile_info = datafile_obj
        except Exception as e:
            import warnings
            warnings.warn(f"Could not parse DataFileInfo segment: {e}")

        if skip_particles:
            return result

        for seg in segments:
            try:
                segment_obj = _deserialize_at(f, seg.offset, seg.count)
                if not isinstance(segment_obj, dict):
                    continue
                if seg.segment_type in (SEG_ALL_PARTICLE_DATA, SEG_SMART_TRIGGER_DATA):
                    result.particles.extend(_extract_particles(segment_obj))
                elif seg.segment_type == SEG_IIF_PARTICLE_DATA:
                    result.imaged_particles.extend(_extract_iif_particles(segment_obj))
            except Exception as e:
                import warnings
                warnings.warn(
                    f"Could not parse segment at offset 0x{seg.offset:x}: {e}"
                )

    result.particles.sort(key=lambda p: p.id)
    result.imaged_particles.sort(key=lambda p: p.id)
    return result
