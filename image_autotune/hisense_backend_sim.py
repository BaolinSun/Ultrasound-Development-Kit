"""Offline forward model of the Hisense back-end display chain, driven by raw BC0 data.

Algo_BC0.bin is tapped upstream of the whole back end: changing TGC leaves it unchanged to
within frame noise while the displayed image moves by more than 20 dB. That makes BC0 a
parameter-independent observation of the tissue, and lets every back-end knob be simulated
offline instead of round-tripping through the console.

BC0 is already log compressed, so the chain below is affine in dB:

    display_dB = BC0 / COUNTS_PER_DB + depth_response_dB(z) + tgc_dB(z) + gain_dB
    gray       = clip((display_dB - (reference_dB - DR)) / DR * 255, 0, 255)

depth_response_dB(z) is a measured per-depth correction curve, not a fudge factor. BC0 sits
upstream of processing that is itself depth dependent - spatial compounding carries explicit
depth-varying weights (BSpatialCompWeightCoe, BSpatialCompDepthThreshold) and the near field
is blanked (BDeadPoints) - so BC0 and the display do not share a depth profile. Without this
term a pure affine model misses by up to 26 gray levels; with it, held-out TGC settings are
reproduced to 0.4-1.0 dB. Calibrate it once from a flat-TGC capture.

Unlike the frame-max normalisation used by the USB pipeline's apply_bmode_mapping(), the
reference level here is absolute. A max-normalised mapping would make the rendered image
depend on the single brightest pixel of the frame, which couples every band to a bright
specular target and destroys the repeatability a feedback loop needs.

Calibration constants are fitted by calibrate_tgc_db_per_level(), calibrate_counts_per_db()
and calibrate_depth_response() from the TGC sweep in data/hisense_medical.
"""

import argparse
from pathlib import Path

import numpy as np

from hisense_loader import (
    DEFAULT_DATA_DIR,
    NUM_TGC_BANDS,
    band_centres,
    band_edges,
    crop_image_area,
    find_captures,
    load_capture,
    load_screenshot,
    crop_capture_image,
)


TGC_MIN_LEVEL = 0
TGC_MAX_LEVEL = 255
TGC_CENTER_LEVEL = 127

# Fitted from the 20260814 TGC sweep; RMS residual 0.26 dB over the unsaturated sliders.
DEFAULT_DB_PER_LEVEL = 0.06559
DEFAULT_COUNTS_PER_DB = 762.5
# Top of the display window in BC0-dB, for the sweep's gain level (BUIGainLevel 75).
DEFAULT_REFERENCE_DB = 84.58
CALIBRATION_GAIN_LEVEL = 75

# Provenance, stated plainly: this bracket was not fitted. In the 20260814 ramp pair, sliders
# 78/114/146/178 reproduced across the two opposite ramps to within 1-2 gray while 6/42/210/242
# disagreed by 4-13, so the bracket is a round interval drawn just outside the four that agreed.
#
# An earlier comment here blamed display clipping. That was wrong: crushed and saturated
# fractions are 0.0 at every excluded point. The 20260819 sweep identified the real cause as a
# nonlinear display response, and it also shows this constant is the wrong shape for the job.
# The two ramps sample each slider at two different depths; extreme sliders happened to land on
# dark bands (gray 2-12) and middle sliders on brighter ones (gray 17-36), and the disagreement
# tracks that brightness, not the slider value (r = -0.82). The real validity limit is a
# brightness bound: against the recovered response, the linear gray-to-dB model holds to 25%
# only above roughly gray 44, and to 10% only above roughly gray 64.
#
# So this bracket is a blunt stand-in that happens to keep the solver near the operating point
# the linear fit was made at. Wiring hisense_display_response into the model replaces it; see
# that module. Widening it without doing so extrapolates a known-wrong slope further.
CALIBRATED_LEVEL_RANGE = (70, 185)

GRAY_MAX = 255.0


def bc0_to_db(bc0, counts_per_db=DEFAULT_COUNTS_PER_DB):
    """Convert raw BC0 counts to dB."""
    return np.asarray(bc0, dtype=np.float64) / float(counts_per_db)


def db_to_bc0(db, counts_per_db=DEFAULT_COUNTS_PER_DB):
    """Convert dB back to raw BC0 counts."""
    return np.asarray(db, dtype=np.float64) * float(counts_per_db)


def tgc_level_to_db(levels, db_per_level=DEFAULT_DB_PER_LEVEL, center=TGC_CENTER_LEVEL):
    """Convert TGC slider levels to the additive dB gain they apply to the display."""
    return (np.asarray(levels, dtype=np.float64) - float(center)) * float(db_per_level)


def tgc_db_to_level(db, db_per_level=DEFAULT_DB_PER_LEVEL, center=TGC_CENTER_LEVEL):
    """Convert a desired additive dB gain to the nearest valid TGC slider level."""
    levels = np.asarray(db, dtype=np.float64) / float(db_per_level) + float(center)
    return np.clip(np.round(levels), TGC_MIN_LEVEL, TGC_MAX_LEVEL).astype(np.int32)


def expand_tgc_to_depth(levels, num_points, db_per_level=DEFAULT_DB_PER_LEVEL):
    """Interpolate the eight TGC slider levels onto a per-depth dB gain curve.

    Gains are placed at the band centres and interpolated linearly, held flat outside the
    outermost centres, which matches the smooth curve the console draws over the image.
    """
    levels = np.asarray(levels, dtype=np.float64).reshape(-1)
    centres = band_centres(num_points, levels.size)
    gains_db = tgc_level_to_db(levels, db_per_level)
    return np.interp(np.arange(int(num_points)), centres, gains_db)


def apply_tgc(bc0_db, tgc_levels, db_per_level=DEFAULT_DB_PER_LEVEL):
    """Add the depth-dependent TGC gain to a (depth, line) dB image."""
    bc0_db = np.asarray(bc0_db, dtype=np.float64)
    curve = expand_tgc_to_depth(tgc_levels, bc0_db.shape[0], db_per_level)
    return bc0_db + curve[:, None]


def db_to_gray(db_image, dynamic_range_db, reference_db=DEFAULT_REFERENCE_DB, graymap_lut=None):
    """Map an absolute dB image onto 0..255 display gray, optionally through a graymap LUT."""
    dynamic_range_db = max(1.0, float(dynamic_range_db))
    floor_db = float(reference_db) - dynamic_range_db
    gray = (np.asarray(db_image, dtype=np.float64) - floor_db) / dynamic_range_db * GRAY_MAX
    gray = np.clip(np.round(gray), 0.0, GRAY_MAX).astype(np.uint8)
    return gray if graymap_lut is None else np.asarray(graymap_lut, dtype=np.uint8)[gray]


def scan_convert_linear(image, out_height, out_width):
    """Resample a (depth, line) image onto the display raster.

    L15-4 is a linear array (ProbeType 1, ProbeRadius 0, BShape 1), so scan conversion is a
    plain separable resample; no polar-to-Cartesian mapping is involved.
    """
    from PIL import Image

    array = np.asarray(image, dtype=np.float32)
    resized = Image.fromarray(array, mode="F").resize(
        (int(round(out_width)), int(round(out_height))), Image.BILINEAR
    )
    return np.asarray(resized, dtype=np.float64)


def render_db(
    bc0,
    tgc_levels=None,
    gain_db=0.0,
    depth_response_db=None,
    counts_per_db=DEFAULT_COUNTS_PER_DB,
    db_per_level=DEFAULT_DB_PER_LEVEL,
):
    """Render raw BC0 to an absolute dB image without the display clip, for metric work."""
    db_image = bc0_to_db(bc0, counts_per_db)
    if depth_response_db is not None:
        response = np.asarray(depth_response_db, dtype=np.float64).reshape(-1)
        if response.size != db_image.shape[0]:
            raise ValueError(
                f"depth_response_db length {response.size} does not match BC0 depth {db_image.shape[0]}"
            )
        db_image = db_image + response[:, None]
    if tgc_levels is not None:
        db_image = apply_tgc(db_image, tgc_levels, db_per_level)
    return db_image + float(gain_db)


def render(
    bc0,
    tgc_levels=None,
    gain_db=0.0,
    dynamic_range_db=67.0,
    depth_response_db=None,
    reference_db=DEFAULT_REFERENCE_DB,
    counts_per_db=DEFAULT_COUNTS_PER_DB,
    db_per_level=DEFAULT_DB_PER_LEVEL,
    graymap_lut=None,
    out_shape=None,
):
    """Render raw BC0 through the simulated back end into a uint8 display image.

    gain_db is an absolute dB offset. The console's BUIGainLevel is *not* yet calibrated to
    dB (that needs sweep S2 of the acquisition protocol), so callers must pass dB directly;
    reference_db as shipped corresponds to BUIGainLevel 75.
    """
    db_image = render_db(bc0, tgc_levels, gain_db, depth_response_db, counts_per_db, db_per_level)
    if out_shape is not None:
        db_image = scan_convert_linear(db_image, out_shape[0], out_shape[1])
    return db_to_gray(db_image, dynamic_range_db, reference_db, graymap_lut)


def gray_to_db(gray, dynamic_range_db, reference_db=DEFAULT_REFERENCE_DB):
    """Invert db_to_gray for unclipped pixels."""
    dynamic_range_db = max(1.0, float(dynamic_range_db))
    return np.asarray(gray, dtype=np.float64) * dynamic_range_db / GRAY_MAX + (
        float(reference_db) - dynamic_range_db
    )


def calibrate_depth_response(
    capture,
    reference_db=None,
    counts_per_db=None,
    gray_low=10,
    gray_high=245,
    min_valid_pixels=30,
    db_per_level=DEFAULT_DB_PER_LEVEL,
):
    """Measure the per-depth dB correction between BC0 and the displayed image.

    The capture must have a flat TGC curve, so that the TGC contributes one constant instead
    of a depth-dependent term. That constant is then subtracted, which matters because the
    console has no numeric TGC entry: the operator drags sliders or taps a preset, so the flat
    reference will not necessarily sit at the neutral level. Without the subtraction the
    reference's own gain is folded into the curve and then applied a second time by render().

    Rows whose display pixels are clipped carry no information and are interpolated across;
    the returned mask flags which BC0 depth samples were measured directly.

    Returns (depth_response_db, measured_mask), both on the BC0 depth grid.
    """
    if not is_flat_tgc(capture):
        raise ValueError("Depth-response calibration needs a capture with a flat TGC curve")

    if counts_per_db is None or reference_db is None:
        fitted_counts, fitted_reference, _ = calibrate_counts_per_db(capture)
        counts_per_db = fitted_counts if counts_per_db is None else counts_per_db
        reference_db = fitted_reference if reference_db is None else reference_db

    screen = crop_capture_image(capture)[0]
    dynamic_range_db = float(capture.dynamic_range_level)
    screen_db = gray_to_db(screen, dynamic_range_db, reference_db)
    bc0_db = bc0_to_db(scan_convert_linear(capture.bc0, screen.shape[0], screen.shape[1]), counts_per_db)

    usable = (screen > gray_low) & (screen < gray_high)
    reference_gain_db = float(tgc_level_to_db(capture.tgc_levels[0], db_per_level))
    residual = screen_db - bc0_db - reference_gain_db
    rows = np.arange(screen.shape[0])
    measured = np.array([usable[row].sum() >= min_valid_pixels for row in rows])
    if measured.sum() < 2:
        raise ValueError("Too few unclipped rows to calibrate the depth response")

    curve = np.full(screen.shape[0], np.nan)
    for row in rows[measured]:
        curve[row] = np.median(residual[row][usable[row]])
    curve = np.interp(rows, rows[measured], curve[measured])

    # Move from the display raster onto the BC0 depth grid.
    num_points = capture.bc0.shape[0]
    bc0_rows = np.linspace(0, screen.shape[0] - 1, num_points)
    response = np.interp(bc0_rows, rows, curve)
    response_mask = np.interp(bc0_rows, rows, measured.astype(float)) > 0.5
    return response, response_mask


def _band_medians(image, num_bands=NUM_TGC_BANDS):
    """Median of each depth band of a (depth, ...) image."""
    edges = band_edges(image.shape[0], num_bands)
    return np.array([np.median(image[edges[k]:edges[k + 1]]) for k in range(num_bands)])


def is_flat_tgc(capture):
    """True if every TGC slider of the capture sits at the same level."""
    return len(set(np.asarray(capture.tgc_levels).tolist())) == 1


def calibrate_tgc_db_per_level(
    captures, level_range=CALIBRATED_LEVEL_RANGE, db_per_level=DEFAULT_DB_PER_LEVEL
):
    """Fit dB-per-slider-level from one or more TGC sweeps.

    Captures are grouped by (gain level, dynamic range), and each group is referenced to its
    own flat-TGC capture. That is what lets a low-end sweep be acquired at a raised gain: the
    global gain offset cancels exactly in the flat-referenced delta, so groups acquired at
    different gains can be pooled into a single fit. Groups without a flat reference, and
    groups that are entirely flat, contribute nothing and are skipped.

    Where a group holds several flat captures (a repeated baseline), their mean is used as the
    reference so the repeats improve it rather than being treated as measurements. Each flat
    carries its own constant gain, which is removed before averaging: the console has no
    numeric TGC entry, so repeated flat references set by dragging sliders will not land on
    exactly the same level. That removal uses the db_per_level argument as a first-order
    correction; it is a fraction of a dB and does not meaningfully feed back into the fit.

    Returns (db_per_level, intercept_db, rms_residual_db, num_points). intercept_db is the
    fitted gain at slider TGC_CENTER_LEVEL; a value near zero means the swept measurements
    extrapolate linearly back to zero gain at the centre, i.e. it is a linearity and
    consistency check on the anchor, not independent proof that 127 is the neutral point.
    """
    captures = list(captures)
    if not captures:
        raise ValueError("TGC calibration needs at least one capture")

    groups = {}
    for capture in captures:
        groups.setdefault((capture.gain_level, capture.dynamic_range_level), []).append(capture)

    levels, gains_db = [], []
    for (_, dynamic_range_level), group in sorted(groups.items()):
        flats = [capture for capture in group if is_flat_tgc(capture)]
        swept = [capture for capture in group if not is_flat_tgc(capture)]
        if not flats or not swept:
            continue

        gray_per_db = GRAY_MAX / float(dynamic_range_level)
        ref_bands = np.mean(
            [
                _band_medians(crop_capture_image(flat)[0])
                - gray_per_db * float(tgc_level_to_db(flat.tgc_levels[0], db_per_level))
                for flat in flats
            ],
            axis=0,
        )
        for capture in swept:
            bands = _band_medians(crop_capture_image(capture)[0])
            for level, delta_gray in zip(capture.tgc_levels, bands - ref_bands):
                if level_range[0] <= level <= level_range[1]:
                    levels.append(float(level))
                    gains_db.append(delta_gray / gray_per_db)

    if len(levels) < 2:
        raise ValueError(
            "Not enough unsaturated TGC samples to fit; check that each gain group has both a "
            "flat-TGC reference and a swept capture, or widen level_range"
        )

    centred = np.asarray(levels) - TGC_CENTER_LEVEL
    design = np.vstack([centred, np.ones_like(centred)]).T
    (slope, intercept), *_ = np.linalg.lstsq(design, np.asarray(gains_db), rcond=None)
    residual = np.asarray(gains_db) - design @ [slope, intercept]
    return float(slope), float(intercept), float(np.sqrt(np.mean(residual**2))), len(levels)


def calibrate_counts_per_db(capture, gray_low=5, gray_high=250):
    """Fit BC0 counts per dB, and the display reference level, from one capture.

    Uses the unsaturated middle half of the image, where displayed gray is affine in BC0.
    Returns (counts_per_db, reference_db, slope_gray_per_count).
    """
    screen = crop_capture_image(capture)[0]
    resampled = scan_convert_linear(capture.bc0, screen.shape[0], screen.shape[1])

    start, stop = 3 * screen.shape[0] // 8, 5 * screen.shape[0] // 8
    counts = resampled[start:stop].ravel()
    gray = screen[start:stop].ravel()
    usable = (gray > gray_low) & (gray < gray_high)
    if usable.sum() < 100:
        raise ValueError("Not enough unsaturated pixels to fit the display reference")

    slope, offset = np.polyfit(counts[usable], gray[usable], 1)
    dynamic_range_db = float(capture.dynamic_range_level)
    counts_per_db = (GRAY_MAX / dynamic_range_db) / slope
    # gray == GRAY_MAX marks the top of the display window.
    reference_db = ((GRAY_MAX - offset) / slope) / counts_per_db
    return float(counts_per_db), float(reference_db), float(slope)


def flat_tgc_capture(captures):
    """Return the capture whose TGC curve is flat, which is the calibration reference."""
    for capture in captures:
        if is_flat_tgc(capture):
            return capture
    raise ValueError("No flat-TGC capture available to use as the calibration reference")


def validate_capture(capture, depth_response_db, reference_db, counts_per_db, db_per_level=DEFAULT_DB_PER_LEVEL):
    """Compare a simulated render of one capture against its own console screenshot.

    Returns (actual_bands, predicted_bands) as per-TGC-band median gray levels.
    """
    actual = crop_capture_image(capture)[0]
    predicted = render(
        capture.bc0,
        tgc_levels=capture.tgc_levels,
        dynamic_range_db=float(capture.dynamic_range_level),
        depth_response_db=depth_response_db,
        reference_db=reference_db,
        counts_per_db=counts_per_db,
        db_per_level=db_per_level,
        out_shape=actual.shape,
    ).astype(np.float64)
    return _band_medians(actual), _band_medians(predicted)


def build_parser():
    parser = argparse.ArgumentParser(description="Fit Hisense back-end model constants from a TGC sweep.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="Directory holding capture folders.")
    return parser


def main():
    args = build_parser().parse_args()
    captures = [load_capture(path) for path in find_captures(args.data_dir)]
    if not captures:
        raise SystemExit(f"No captures found under {args.data_dir}")

    slope, intercept, rms, count = calibrate_tgc_db_per_level(captures)
    print("TGC slider calibration")
    print(f"  dB per level    : {slope:.5f}  (shipped default {DEFAULT_DB_PER_LEVEL:.5f})")
    print(f"  span 0..255     : {slope * 255:.2f} dB, centre +/-{slope * 127.5:.2f} dB")
    print(f"  intercept at {TGC_CENTER_LEVEL}: {intercept:+.3f} dB  (near zero = linear back to the anchor)")
    print(f"  RMS residual    : {rms:.3f} dB over {count} samples")

    flat = flat_tgc_capture(captures)
    counts_per_db, reference_db, slope_gray = calibrate_counts_per_db(flat)
    print(f"\nDisplay calibration from {flat.name} (gain {flat.gain_level}, DR {flat.dynamic_range_level})")
    print(f"  counts per dB   : {counts_per_db:.1f}  (shipped default {DEFAULT_COUNTS_PER_DB:.1f})")
    print(f"  reference dB    : {reference_db:.2f}  (shipped default {DEFAULT_REFERENCE_DB:.2f})")
    print(f"  gray per count  : {slope_gray:.6f}")
    print(f"  BC0 span        : {(flat.bc0.max() - flat.bc0.min()) / counts_per_db:.1f} dB")

    response, measured = calibrate_depth_response(flat, reference_db, counts_per_db)
    print(f"\nDepth response from {flat.name}")
    print(f"  measured directly: {measured.sum()}/{measured.size} depth samples")
    print(f"  range            : {response.min():+.1f} .. {response.max():+.1f} dB")

    print("\nSimulated vs console screenshot, per TGC band (gray levels)")
    gray_per_db = GRAY_MAX / float(flat.dynamic_range_level)
    for capture in captures:
        actual, predicted = validate_capture(capture, response, reference_db, counts_per_db, slope)
        error = np.abs(predicted - actual)
        held_out = "reference" if capture.path == flat.path else "held out"
        print(f"  {capture.name} ({held_out})")
        print(f"    sliders  : {capture.tgc_levels.tolist()}")
        print(f"    actual   : {[round(v) for v in actual]}")
        print(f"    predicted: {[round(v) for v in predicted]}")
        print(f"    mean |error| = {error.mean():.1f} gray ({error.mean() / gray_per_db:.2f} dB)")


if __name__ == "__main__":
    main()
