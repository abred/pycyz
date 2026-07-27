"""Command-line interface for pycyz."""

from __future__ import annotations

import argparse
import csv
import os

from .cyz import (
    CyzFileData,
    ImagedParticle,
    Particle,
    SEG_TYPE_NAMES,
    _get_field,
    dotnet_ticks_to_datetime,
    read_cyz,
)


def cmd_info(cyz: CyzFileData, path: str) -> None:
    """Print a summary of the CYZ file."""
    file_size = os.path.getsize(path)
    print(f"File: {path}")
    print(f"Size: {file_size:,} bytes")
    print()

    print(f"Segments: {len(cyz.segments)}")
    if cyz.df_info_segment:
        seg = cyz.df_info_segment
        print(
            f"  DataFileInfo: offset=0x{seg.offset:x}, "
            f"size={seg.count:,} bytes"
        )
    for i, seg in enumerate(cyz.segments):
        print(
            f"  [{i}] {SEG_TYPE_NAMES.get(seg.segment_type, '?')}: "
            f"offset=0x{seg.offset:x}, size={seg.count:,} bytes"
        )
    print()

    print(f"Particles: {len(cyz.particles)}")
    print(f"Imaged particles (IIF): {len(cyz.imaged_particles)}")
    total_image_bytes = sum(len(p.image_data) for p in cyz.imaged_particles)
    if cyz.imaged_particles:
        print(f"Total image data: {total_image_bytes:,} bytes")
    print()

    channels = cyz.channel_names
    if channels:
        print(f"Channels ({len(channels)}): {', '.join(channels)}")
    print()

    scale = cyz.image_scale_um_per_pixel
    if scale is not None:
        print("Image calibration:")
        print(f"  Scale: {scale:.6g} um/pixel")
        if cyz.pixel_pitch_um is not None:
            print(f"  Sensor pixel pitch: {cyz.pixel_pitch_um:.6g} um")
        if cyz.optical_magnification is not None:
            print(f"  Optical magnification: {cyz.optical_magnification:.6g}x")
        roi = cyz.image_roi
        if roi:
            left, top, w, h = roi
            print(
                f"  Camera ROI: {w}x{h} px at ({left},{top})"
                f"  =  {w * scale:.1f} x {h * scale:.1f} um"
            )
        if cyz.camera_name:
            print(f"  Camera: {cyz.camera_name}")
        print()

    mi = cyz.measurement_info
    if mi:
        start = _get_field(mi, "MeasurementStartTime")
        if isinstance(start, int):
            start = dotnet_ticks_to_datetime(start)
            print(f"Measurement start: {start}")

    mr = cyz.measurement_results
    if mr:
        if mr.duration_s is not None:
            print(f"Duration: {mr.duration_s:.1f} s")
        if mr.counted_particles is not None:
            print(f"Particles counted: {mr.counted_particles:,}")
        if mr.smart_triggered_particles is not None:
            print(f"Smart-triggered particles: {mr.smart_triggered_particles:,}")
        if mr.picture_count is not None:
            print(f"Pictures taken: {mr.picture_count:,}")

    sl = cyz.sensor_logs
    available = sl.available()
    if available:
        print(f"\nSensor logs ({len(available)} sensors): {', '.join(available)}")
        # Print latest reading for each available sensor
        for name in available:
            readings = getattr(sl, name)
            if readings:
                last = readings[-1]
                print(f"  {name}: {last.value:.3g}  (last at {last.time})")

    gps = cyz.gps_data
    if gps:
        print(f"\nGPS coordinates: {len(gps)} fixes")
        for i, g in enumerate(gps[:3]):
            raw = _get_field(g, "_string", "")
            print(f"  [{i}] {raw}")
        if len(gps) > 3:
            print(f"  ... and {len(gps) - 3} more")

    cs = cyz.cyto_settings
    if cs:
        sn = _get_field(cs, "SerNr") or _get_field(cs, "_serialNumber")
        if sn:
            print(f"\nSerial number: {sn}")


def cmd_extract_images(cyz: CyzFileData, out_dir: str) -> None:
    """Extract IIF particle images to a directory."""
    os.makedirs(out_dir, exist_ok=True)
    count = 0
    for idx, p in enumerate(cyz.imaged_particles):
        if not p.image_data:
            continue
        ext = ".png" if p.image_data[:4] == b"\x89PNG" else ".jpg"
        filename = f"particle_{idx:04d}_id{p.id}{ext}"
        filepath = os.path.join(out_dir, filename)
        with open(filepath, "wb") as f:
            f.write(p.image_data)
        count += 1
    print(f"Extracted {count} images to {out_dir}")


_PARAM_FIELDS = (
    "length", "total", "maximum", "average",
    "fill_factor", "asymmetry", "centre_of_gravity", "inertia", "cell_count",
)


def cmd_csv(cyz: CyzFileData, csv_path: str) -> None:
    """Export particle data and computed pulse parameters to CSV."""
    all_particles: list[Particle] = []
    all_particles.extend(cyz.particles)
    all_particles.extend(cyz.imaged_particles)
    all_particles.sort(key=lambda p: p.id)

    channel_names = cyz.channel_names
    scale = cyz.image_scale_um_per_pixel

    # Image geometry columns are only meaningful when the file has IIF images.
    # These describe the stored image region (a band cropped out of the camera
    # frame), NOT a particle bounding box — hence the "fov" naming for the
    # physical dimensions.
    image_fields = ["image_x", "image_y", "image_width_px", "image_height_px"]
    if scale is not None:
        image_fields += ["image_fov_width_um", "image_fov_height_um"]
    emit_image_cols = bool(cyz.imaged_particles)

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        header = ["particle_id", "time_of_arrival", "has_image", "n_channels"]
        if emit_image_cols:
            header += image_fields
        for name in channel_names:
            for param in _PARAM_FIELDS:
                header.append(f"ch_{name}_{param}")
        writer.writerow(header)

        for p in all_particles:
            is_imaged = isinstance(p, ImagedParticle)
            row: list = [p.id, p.time_of_arrival.isoformat(), is_imaged, len(p.channel_data)]
            if emit_image_cols:
                if is_imaged:
                    x, y, w, h = p.crop_rect
                    row += [x, y, w, h]
                    if scale is not None:
                        row += [w * scale, h * scale]
                else:
                    row += [""] * len(image_fields)
            params_per_ch = p.all_channel_params()
            for i in range(len(channel_names)):
                if i < len(params_per_ch):
                    pp = params_per_ch[i]
                    for field in _PARAM_FIELDS:
                        row.append(getattr(pp, field))
                else:
                    row.extend([""] * len(_PARAM_FIELDS))
            writer.writerow(row)

    print(f"Exported {len(all_particles)} particles to {csv_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pycyz",
        description="Read CytoSense .cyz flow cytometer files (pure Python)",
    )
    parser.add_argument("cyz_file", help="Path to .cyz file")
    parser.add_argument("--info", action="store_true", help="Print file summary")
    parser.add_argument(
        "--extract-images", metavar="DIR", help="Extract IIF images to directory"
    )
    parser.add_argument("--csv", metavar="FILE", help="Export particle metadata to CSV")
    args = parser.parse_args()

    if not any([args.info, args.extract_images, args.csv]):
        args.info = True  # default action

    skip_particles = args.info and not args.extract_images and not args.csv
    cyz = read_cyz(args.cyz_file, skip_particles=skip_particles)

    if args.info:
        cmd_info(cyz, args.cyz_file)
    if args.extract_images:
        cmd_extract_images(cyz, args.extract_images)
    if args.csv:
        cmd_csv(cyz, args.csv)
