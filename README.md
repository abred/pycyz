# pycyz

Pure-Python reader for **CytoSense** `.cyz` flow cytometer files.
No .NET runtime, no proprietary DLLs, no native extensions — only the Python standard library.

---

## Installation

### With uv (recommended)

```bash
uv add pycyz
```

Or install directly from source:

```bash
git clone https://github.com/yourorg/pycyz
cd pycyz
uv sync
```

### With pip

```bash
pip install pycyz
```

---

## Quick start

### As a library

```python
from pycyz import read_cyz

cyz = read_cyz("measurement.cyz")

# Metadata
print(cyz.channel_names)       # ['FWS', 'SWS', 'FL Red', ...]
print(cyz.measurement_info)    # raw dict from the DataFileInfo segment
print(cyz.gps_data)            # list of GPS fix dicts

# Particles
for p in cyz.particles:
    print(p.id, p.time_of_arrival, len(p.channel_data), "channels")
    # p.channel_data[i] is raw bytes — the ADC pulse shape for channel i

# Computed pulse parameters for one particle, all channels
for p in cyz.particles[:5]:
    for i, pp in enumerate(p.all_channel_params()):
        print(
            f"  particle {p.id} ch{i}: "
            f"total={pp.total:.0f} max={pp.maximum:.0f} "
            f"fill={pp.fill_factor:.3f} asym={pp.asymmetry:.3f} "
            f"cog={pp.centre_of_gravity:.2f} inertia={pp.inertia:.3f} "
            f"cells={pp.cell_count:.2f}"
        )

# Or for a single channel
pp = cyz.particles[0].channel_params(0)
print(pp.total, pp.fill_factor)

# IIF imaged particles
for ip in cyz.imaged_particles:
    print(ip.id, ip.crop_rect, len(ip.image_data), "bytes")
    # ip.image_data is a raw JPEG or PNG blob
```

# Measurement results (duration, particle counts)

```python
mr = cyz.measurement_results
print(mr.duration_s, "s,", mr.counted_particles, "particles")
```

# Sensor time-series

```python
sl = cyz.sensor_logs
print("sensors available:", sl.available())
for r in sl.system_temp:          # list[SensorReading]
    print(r.time, r.value, "°C")
```

Skip particle loading if you only need metadata:

```python
cyz = read_cyz("measurement.cyz", skip_particles=True)
print(cyz.measurement_info)
```

### Command line

```bash
# Print file summary (default action)
pycyz measurement.cyz

# Export particle metadata to CSV
pycyz measurement.cyz --csv particles.csv

# Extract IIF images to a directory
pycyz measurement.cyz --extract-images ./images/

# Combine
pycyz measurement.cyz --info --csv particles.csv --extract-images ./images/
```

---

## API reference

| Symbol                                 | Description                                                                                                                         |
| -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `read_cyz(path, skip_particles=False)` | Parse a `.cyz` file; returns `CyzFileData`                                                                                          |
| `CyzFileData`                          | Top-level dataclass holding all parsed data                                                                                         |
| `CyzFileData.particles`                | `list[Particle]` — non-imaged particles                                                                                             |
| `CyzFileData.imaged_particles`         | `list[ImagedParticle]` — particles with IIF images                                                                                  |
| `CyzFileData.channel_names`            | `list[str]` — detector channel names                                                                                                |
| `CyzFileData.measurement_info`         | `dict` — measurement metadata                                                                                                       |
| `CyzFileData.cyto_settings`            | `dict` — instrument configuration                                                                                                   |
| `CyzFileData.gps_data`                 | `list[dict]` — GPS fixes recorded during measurement                                                                                |
| `CyzFileData.measurement_results`      | `MeasurementResults` — duration, particle counts, picture count                                                                     |
| `CyzFileData.sensor_logs`              | `SensorLogs` — timestamped temperature, pressure, flow, concentration                                                               |
| `MeasurementResults`                   | Dataclass: `duration_s`, `counted_particles`, `smart_triggered_particles`, `picture_count`                                          |
| `SensorLogs`                           | Dataclass with one `list[SensorReading]` per sensor; see `.available()`                                                             |
| `SensorReading`                        | Frozen dataclass: `time: datetime`, `value: float`                                                                                  |
| `parse_measurement_results(mi)`        | Parse results from a raw measurement-info dict                                                                                      |
| `parse_sensor_logs(mi)`                | Parse sensor time-series from a raw measurement-info dict                                                                           |
| `Particle.channel_data`                | `list[bytes]` — raw ADC pulse shape per channel                                                                                     |
| `Particle.channel_params(i)`           | Compute `PulseParams` for channel _i_                                                                                               |
| `Particle.all_channel_params()`        | `list[PulseParams]` for every channel                                                                                               |
| `ImagedParticle.image_data`            | `bytes` — JPEG or PNG image blob                                                                                                    |
| `ImagedParticle.crop_rect`             | `(x, y, w, h)` — crop rectangle in the camera frame                                                                                 |
| `compute_params(data)`                 | Compute `PulseParams` from a raw `bytes` pulse                                                                                      |
| `PulseParams`                          | Frozen dataclass: `length`, `total`, `maximum`, `average`, `fill_factor`, `asymmetry`, `centre_of_gravity`, `inertia`, `cell_count` |
| `NRBFReader`                           | Low-level MS-NRBF stream deserializer (exposed for advanced use)                                                                    |
| `dotnet_ticks_to_datetime(ticks)`      | Convert .NET DateTime ticks to `datetime`                                                                                           |

---

## Feature comparison

The two most widely used open-source `.cyz` tools are
[CyzFile-API](https://github.com/Cytobuoy/CyzFile-API) (a .NET library by
CytoBuoy) and [cyz2json](https://github.com/OBAMANEXT/cyz2json) (a .NET CLI
tool that wraps `CyzFile-API` and exports JSON). The table below shows how
`pycyz` compares.

| Feature                                                      |  CyzFile-API   |    cyz2json     |      **pycyz**       |
| ------------------------------------------------------------ | :------------: | :-------------: | :------------------: |
| **Language / runtime**                                       |   C# / .NET    |   C# / .NET 8   |  **Python ≥ 3.10**   |
| **No native dependencies**                                   |       ✗        |        ✗        |        **✓**         |
| Read file segments & header                                  |       ✓        |        ✓        |          ✓           |
| Instrument name & serial number                              |       ✓        |        ✓        |     ✓ (raw dict)     |
| Channel names & configuration                                |       ✓        |        ✓        |          ✓           |
| Measurement start time                                       |       ✓        |        ✓        |          ✓           |
| Particle count (reported)                                    |       ✓        |        ✓        |     ✓ (raw dict)     |
| Particle time of arrival                                     |       ✓        |        ✓        |          ✓           |
| Raw ADC pulse shapes (bytes)                                 |       ✓        |        ✓        |          ✓           |
| IIF particle images (JPEG/PNG)                               |       ✓        | ✓ (base64 JSON) |    ✓ (raw bytes)     |
| IIF crop rectangle                                           |       ✓        |        ✓        |          ✓           |
| GPS coordinates                                              |       ✓        |        —        |     ✓ (raw dict)     |
| Smart-trigger particle data                                  |       ✓        |        ✓        |          ✓           |
| **Computed channel parameters**                              |                |                 |                      |
| &nbsp;&nbsp;Pulse length                                     |       ✓        |        ✓        |          ✓           |
| &nbsp;&nbsp;Total / max / average                            |       ✓        |        ✓        |          ✓           |
| &nbsp;&nbsp;Fill factor & asymmetry                          |       ✓        |        ✓        |          ✓           |
| &nbsp;&nbsp;Centre of gravity / inertia                      |       ✓        |        ✓        |          ✓           |
| &nbsp;&nbsp;Cell count                                       |       ✓        |        ✓        |          ✓           |
| **Sensor readings**                                          |                |                 |                      |
| &nbsp;&nbsp;Temperature (system, PMT, sheath, buoy, laser …) |       ✓        |        ✓        |          ✓           |
| &nbsp;&nbsp;Pressure (absolute / diff.)                      |       ✓        |        ✓        |          ✓           |
| &nbsp;&nbsp;Sheath flow, laser current, voltage, …           |       ✓        |        ✓        |          ✓           |
| **Measurement results**                                      |                |                 |                      |
| &nbsp;&nbsp;Duration, particle count, picture count          |       ✓        |        ✓        |          ✓           |
| &nbsp;&nbsp;Analysed / pumped volume                         |       ✓        |        ✓        |       **✗** †        |
| &nbsp;&nbsp;Concentration                                    |       ✓        |        ✓        |       **✗** †        |
| Particle region / classification                             |       ✓        |        ✓        |        **✗**         |
| Bulk / batch file processing                                 |       —        |        ✓        |        **✗**         |
| Output formats                                               |    .NET API    |      JSON       | Python objects / CSV |
| License                                                      | Modified MIT\* |        —        |         MIT          |

\* CyzFile-API's modified MIT licence prohibits commercial use without
permission from CytoBuoy.

### What pycyz does well

- **Zero dependencies** — install with a single `pip install` or `uv add`; no
  .NET SDK, no OpenCV, no DLLs required.
- **Cross-platform** — works anywhere Python runs (Linux, macOS, Windows).
- **Embeddable** — import directly into notebooks, pipelines, and web services.
- **Transparent** — the full NRBF deserializer is readable Python; you can
  inspect the raw object graph if the high-level API doesn't expose something
  you need.

† Volume and concentration are computed properties in `CyzFile-API`
(`pumped_volume = MeasureTime × SamplePumpSpeed`); they are not stored as
scalars in the `.cyz` file. The required values (`MeasureTime` in
`MeasurementResults.duration_s`, pump speed in `CyzFileData.cyto_settings`)
are both available — the arithmetic is left to the caller.

### What pycyz does not (yet) do

- **Physical pulse length** — `CyzFile-API` converts sample counts to
  micrometres using the instrument's sample-rate and laser-beam-width, plus a
  convolution correction. `pycyz` exposes the raw sample count as
  `PulseParams.length`; converting to physical units requires the instrument
  configuration values from `CyzFileData.cyto_settings`.
- **Particle classification** — neither `CyzFile-API` nor `cyz2json` implement
  gating algorithms; both expose region/classification data stored in the file.
  `pycyz` does not yet parse this data.

---

## Development

```bash
git clone https://github.com/yourorg/pycyz
cd pycyz
uv sync
uv run pytest
uv run ruff check src/
```

---

## License

MIT — see `LICENSE`.
