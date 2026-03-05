"""
Ball Extraction Utilities

Extracts ball from images using LED reflection detection and dynamic radius estimation.
"""

import os
import cv2
import numpy as np
from itertools import combinations


def _check_rectangle(points, angle_tolerance=25, side_ratio_tolerance=0.4):
    """
    Check if 4 points form a roughly rectangular shape.
    """
    if len(points) != 4:
        return False

    pts = np.array([(p[0], p[1]) for p in points], dtype=np.float32)
    hull = cv2.convexHull(pts, returnPoints=True)
    if len(hull) != 4:
        return False

    hull_pts = hull.reshape(-1, 2).astype(np.float32)

    sides = []
    for i in range(4):
        p1 = hull_pts[i]
        p2 = hull_pts[(i + 1) % 4]
        side_len = np.linalg.norm(p2 - p1)
        sides.append(side_len)

    side0_2_ratio = abs(sides[0] - sides[2]) / max(sides[0], sides[2]) if max(sides[0], sides[2]) > 0 else 1.0
    side1_3_ratio = abs(sides[1] - sides[3]) / max(sides[1], sides[3]) if max(sides[1], sides[3]) > 0 else 1.0

    if side0_2_ratio > side_ratio_tolerance or side1_3_ratio > side_ratio_tolerance:
        return False

    angles_ok = 0
    for i in range(4):
        p1 = hull_pts[i]
        p2 = hull_pts[(i + 1) % 4]
        p3 = hull_pts[(i + 2) % 4]

        v1 = p2 - p1
        v2 = p3 - p2

        norm1 = np.linalg.norm(v1)
        norm2 = np.linalg.norm(v2)
        if norm1 > 0 and norm2 > 0:
            cos_angle = np.dot(v1, v2) / (norm1 * norm2)
            cos_angle = np.clip(cos_angle, -1.0, 1.0)
            angle = np.degrees(np.arccos(cos_angle))
            if abs(angle - 90) <= angle_tolerance:
                angles_ok += 1

    return angles_ok >= 3


def _find_rectangle_corners(spots):
    """
    Find 4 spots that form a rectangle from a list of candidate spots.
    """
    if len(spots) < 4:
        return None

    best_rect = None
    best_score = float("inf")

    for combo in combinations(spots, 4):
        if _check_rectangle(combo):
            pts = np.array([(p[0], p[1]) for p in combo], dtype=np.float32)
            side1 = np.linalg.norm(pts[0] - pts[1])
            side2 = np.linalg.norm(pts[1] - pts[2])
            side3 = np.linalg.norm(pts[2] - pts[3])
            side4 = np.linalg.norm(pts[3] - pts[0])
            sides = [side1, side2, side3, side4]
            mean_side = np.mean(sides)
            variance = np.var(sides) / (mean_side**2) if mean_side > 0 else float("inf")
            if variance < best_score:
                best_score = variance
                best_rect = combo

    return best_rect


def _estimate_fourth_spot_from_three(three_spots, h, w, v, bright, search_radius=100, min_pixels=5, logger=None):
    """
    Given 3 LED spots that are three corners of a rectangle, estimate the 4th corner position
    and search for the brightest cluster of pixels in that region.
    """
    p0, p1, p2 = [(s[0], s[1]) for s in three_spots]
    candidates = [
        (int(p0[0] + p1[0] - p2[0]), int(p0[1] + p1[1] - p2[1])),
        (int(p0[0] + p2[0] - p1[0]), int(p0[1] + p2[1] - p1[1])),
        (int(p1[0] + p2[0] - p0[0]), int(p1[1] + p2[1] - p0[1])),
    ]
    best_spot = None
    best_brightness = -1.0
    for (dx, dy) in candidates:
        x1 = max(0, dx - search_radius)
        x2 = min(w, dx + search_radius + 1)
        y1 = max(0, dy - search_radius)
        y2 = min(h, dy + search_radius + 1)
        roi = bright[y1:y2, x1:x2]
        v_roi = v[y1:y2, x1:x2]
        ys, xs = np.where(roi > 0)
        if len(ys) < min_pixels:
            continue
        cx = float(xs.mean()) + x1
        cy = float(ys.mean()) + y1
        area = len(ys)
        brightness = float(v_roi[roi > 0].mean())
        if brightness > best_brightness:
            best_brightness = brightness
            best_spot = (int(round(cx)), int(round(cy)), brightness, area)
    if logger and best_spot is not None:
        logger.info(
            f"Estimated 4th LED spot from 3 at ({best_spot[0]}, {best_spot[1]}), "
            f"brightness={best_spot[2]:.1f}, area={best_spot[3]}"
        )
    return best_spot


def _find_ball_center_from_led_spots(
    img_bgr,
    bright_thresh=240,
    min_area=400,
    max_area=5000,
    max_saturation=180,
    logger=None,
):
    """
    Find ball center by detecting 4 LED reflection spots that form a rectangle.
    LED spots must be pure white (high brightness, low saturation).
    """
    h, w = img_bgr.shape[:2]
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2]
    s = hsv[:, :, 1]

    _, bright = cv2.threshold(v, bright_thresh, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    bright = cv2.morphologyEx(bright, cv2.MORPH_OPEN, kernel, iterations=2)
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, kernel, iterations=1)

    contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    spots = []

    for c in contours:
        a = cv2.contourArea(c)
        if min_area <= a <= max_area:
            M = cv2.moments(c)
            if M["m00"] != 0:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])
                mask_spot = np.zeros((h, w), dtype=np.uint8)
                cv2.drawContours(mask_spot, [c], -1, 255, -1)
                brightness = v[mask_spot > 0].mean()
                saturation = s[mask_spot > 0].mean()
                if brightness >= bright_thresh and saturation <= max_saturation:
                    spots.append((cx, cy, brightness, a))
                elif logger and brightness >= bright_thresh:
                    logger.debug(
                        f"Rejected spot at ({cx}, {cy}): brightness={brightness:.1f} OK, "
                        f"but saturation={saturation:.1f} > {max_saturation} (not white enough)"
                    )

    if len(spots) == 3:
        fourth = _estimate_fourth_spot_from_three(spots, h, w, v, bright, search_radius=100, min_pixels=5, logger=logger)
        if fourth is not None:
            spots.append(fourth)
            if logger:
                logger.info("Recovered 4th LED spot from 3-spot geometry + brightest region")

    if len(spots) < 4:
        if logger:
            logger.warning(f"Found only {len(spots)} white LED spots, need at least 4 for rectangle")
        return None

    if logger:
        logger.info(
            f"Found {len(spots)} white LED reflection spots "
            f"(brightness >= {bright_thresh}, saturation <= {max_saturation})"
        )

    spots.sort(key=lambda spt: spt[2], reverse=True)
    candidate_spots = spots[: min(10, len(spots))]
    rectangle_spots = _find_rectangle_corners(candidate_spots)
    if rectangle_spots is None:
        if logger:
            logger.warning("Could not find 4 spots forming a rectangle, using top 4 brightest")
        rectangle_spots = tuple(candidate_spots[:4])

    sorted_by_y = sorted(rectangle_spots, key=lambda spt: spt[1], reverse=True)
    bottom_two = sorted_by_y[:2]
    center_x = int((bottom_two[0][0] + bottom_two[1][0]) / 2)
    center_y = int((bottom_two[0][1] + bottom_two[1][1]) / 2)
    center = (center_x, center_y)

    if logger:
        logger.info(f"Ball center (from LED spots): {center}")

    return center, bottom_two, list(rectangle_spots)


def _detect_radius_from_edges(img_bgr, center, max_radius=None, expected_ball_diameter_px=None, logger=None):
    """
    Detect ball radius by finding the ball edge in the image.
    """
    h, w = img_bgr.shape[:2]
    cx, cy = center
    if max_radius is None:
        max_radius = min(cx, w - cx, cy, h - cy)

    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)
    l_channel = lab[:, :, 0]

    num_rays = 5
    ray_width = 7
    ray_offsets = np.linspace(-ray_width, ray_width, num_rays)
    detected_radii = []

    def _detect_edge_radius_1d(line_1d):
        line_1d = cv2.GaussianBlur(line_1d.reshape(-1, 1), (5, 1), 0).flatten()
        gradient = np.gradient(line_1d.astype(np.float32))
        gradient_magnitude = np.abs(gradient)
        min_radius_local = 50
        if len(gradient_magnitude) <= min_radius_local:
            return None
        outer_region = gradient_magnitude[min_radius_local:]
        if len(outer_region) == 0:
            return None
        max_grad_idx_outer = np.argmax(outer_region) + min_radius_local
        inner_region = gradient_magnitude[:min_radius_local]
        if len(inner_region) > 0:
            max_grad_inner = np.max(inner_region)
            max_grad_outer = np.max(outer_region)
            if max_grad_inner > max_grad_outer * 1.5:
                max_grad_idx = np.argmax(inner_region)
            else:
                max_grad_idx = max_grad_idx_outer
        else:
            max_grad_idx = max_grad_idx_outer
        threshold = np.max(gradient_magnitude) * 0.2
        if gradient_magnitude[max_grad_idx] > threshold:
            return max_grad_idx
        return None

    for ray_offset in ray_offsets:
        ray_x = int(cx + ray_offset)
        if ray_x < 0 or ray_x >= w:
            continue
        x_start = max(0, ray_x - ray_width // 2)
        x_end = min(w, ray_x + ray_width // 2 + 1)
        y_start = max(0, cy - max_radius)
        y_end = cy
        line_segment = l_channel[y_start:y_end, x_start:x_end]
        if line_segment.size == 0:
            continue
        line_1d = np.mean(line_segment, axis=1)[::-1]
        r = _detect_edge_radius_1d(line_1d)
        if r is not None:
            detected_radii.append(r)

    for ray_offset in ray_offsets:
        ray_y = int(cy + ray_offset)
        if ray_y < 0 or ray_y >= h:
            continue
        x_start = cx
        x_end = min(w, cx + max_radius)
        line_segment = l_channel[ray_y, x_start:x_end]
        if line_segment.size > 0:
            line_1d = line_segment.astype(np.float32)
            r = _detect_edge_radius_1d(line_1d)
            if r is not None:
                detected_radii.append(r)
        x_start = max(0, cx - max_radius)
        x_end = cx
        line_segment = l_channel[ray_y, x_start:x_end]
        if line_segment.size > 0:
            line_1d = line_segment[::-1].astype(np.float32)
            r = _detect_edge_radius_1d(line_1d)
            if r is not None:
                detected_radii.append(r)

    if not detected_radii:
        if logger:
            logger.warning("Could not detect edge from any ray (vertical or horizontal)")
        return None

    raw_radius = int(np.median(detected_radii))
    if expected_ball_diameter_px is None:
        expected_ball_diameter_px = 1278
    expected_radius = expected_ball_diameter_px // 2
    lower_bound = int(expected_radius * 0.7)
    upper_bound = int(expected_radius * 1.3)
    if raw_radius < lower_bound or raw_radius > upper_bound:
        detected_radius = expected_radius
        if logger:
            logger.warning(
                f"Raw radius {raw_radius}px outside [{lower_bound},{upper_bound}]px, "
                f"using expected radius {expected_radius}px instead"
            )
    else:
        detected_radius = int(0.5 * raw_radius + 0.5 * expected_radius)

    detected_radius = min(detected_radius, int(max_radius * 0.95))
    if detected_radius < 50:
        if logger:
            logger.warning(f"Detected radius {detected_radius} seems too small after adjustment")
        return None

    if logger:
        logger.info(f"Detected ball radius: {detected_radius} pixels (raw median: {raw_radius}px)")

    return detected_radius


def extract_ball(
    img,
    filename=None,
    logger=None,
    normalize_size=(1024, 1024),
    fallback_radius=None,
    target_ball_diameter=None,
    preserve_led_spot_size=False,
    max_saturation=220,
    expected_ball_diameter_px=None,
):
    """
    Extract ball from a single image using LED reflection detection and dynamic radius estimation.
    """
    if img is None:
        if logger:
            logger.error("Input image is None")
        return None

    h, w = img.shape[:2]
    rotated_img = img.copy()
    if filename:
        filename_upper = filename.upper()
        if "CAMERA_A" in filename_upper:
            rotated_img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
            if logger:
                logger.debug("Rotated Camera A image 90° counter-clockwise")
        elif "CAMERA_B" in filename_upper:
            rotated_img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
            if logger:
                logger.debug("Rotated Camera B image 90° clockwise")

    h_rot, w_rot = rotated_img.shape[:2]
    result = _find_ball_center_from_led_spots(rotated_img, max_saturation=max_saturation, logger=logger)
    if result is None:
        if logger:
            logger.error("Could not find ball center from LED spots")
        return None

    center, _, _ = result
    cx, cy = center
    if filename:
        filename_upper = filename.upper()
        if "CAMERA_A" in filename_upper:
            cy_adjusted = cy + 12
            radius_adjustment = 5
        elif "CAMERA_B" in filename_upper:
            cy_adjusted = cy + 8
            radius_adjustment = 8
        else:
            cy_adjusted = cy
            radius_adjustment = 0
    else:
        cy_adjusted = cy
        radius_adjustment = 0

    center_adjusted = (cx, cy_adjusted)
    max_radius = min(cx, w_rot - cx, cy_adjusted, h_rot - cy_adjusted)
    detected_radius = _detect_radius_from_edges(
        rotated_img,
        center_adjusted,
        max_radius=max_radius,
        expected_ball_diameter_px=expected_ball_diameter_px,
        logger=logger,
    )

    if detected_radius is None:
        if fallback_radius is not None:
            detected_radius = fallback_radius
            if logger:
                logger.warning(f"Using fallback radius: {detected_radius}px")
        else:
            detected_radius = int(min(h_rot, w_rot) * 0.2)
            if logger:
                logger.warning(f"Dynamic radius detection failed, using image-based estimate: {detected_radius}px")

    detected_radius = detected_radius + radius_adjustment
    detected_radius = max(50, detected_radius)

    mask = np.zeros((h_rot, w_rot), dtype=np.uint8)
    cv2.circle(mask, (cx, cy_adjusted), int(detected_radius), 255, -1)
    result_img = cv2.bitwise_and(rotated_img, rotated_img, mask=mask)
    y1 = max(0, cy_adjusted - int(detected_radius))
    y2 = min(h_rot, cy_adjusted + int(detected_radius))
    x1 = max(0, cx - int(detected_radius))
    x2 = min(w_rot, cx + int(detected_radius))
    cropped_ball = result_img[y1:y2, x1:x2]
    cropped_mask = mask[y1:y2, x1:x2]

    if normalize_size is not None:
        target_w, target_h = normalize_size
        result_norm = np.zeros((target_h, target_w, 3), dtype=np.uint8)
        current_h, current_w = cropped_ball.shape[:2]
        current_ball_diameter = 2 * detected_radius
        if preserve_led_spot_size:
            if normalize_size is not None:
                target_w, target_h = 1400, 1400
                result_norm = np.zeros((target_h, target_w, 3), dtype=np.uint8)
            if current_w > 0 and current_h > 0:
                y_offset = (target_h - current_h) // 2
                x_offset = (target_w - current_w) // 2
                if current_w <= target_w and current_h <= target_h:
                    result_norm[y_offset : y_offset + current_h, x_offset : x_offset + current_w][
                        cropped_mask > 0
                    ] = cropped_ball[cropped_mask > 0]
                else:
                    scale = min(target_w / current_w, target_h / current_h)
                    new_w = int(current_w * scale)
                    new_h = int(current_h * scale)
                    if new_w > 0 and new_h > 0:
                        resized_ball = cv2.resize(cropped_ball, (new_w, new_h), interpolation=cv2.INTER_AREA)
                        resized_mask = cv2.resize(cropped_mask, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
                        y_offset = (target_h - new_h) // 2
                        x_offset = (target_w - new_w) // 2
                        result_norm[y_offset : y_offset + new_h, x_offset : x_offset + new_w][
                            resized_mask > 0
                        ] = resized_ball[resized_mask > 0]
        else:
            if target_ball_diameter is None:
                target_ball_diameter = min(target_w, target_h)
            if current_ball_diameter > 0:
                scale = target_ball_diameter / current_ball_diameter
            else:
                scale = 1.0
            if current_w > 0 and current_h > 0:
                new_w = int(current_w * scale)
                new_h = int(current_h * scale)
                if new_w > 0 and new_h > 0:
                    resized_ball = cv2.resize(cropped_ball, (new_w, new_h), interpolation=cv2.INTER_AREA)
                    resized_mask = cv2.resize(cropped_mask, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
                    y_offset = (target_h - new_h) // 2
                    x_offset = (target_w - new_w) // 2
                    result_norm[y_offset : y_offset + new_h, x_offset : x_offset + new_w][
                        resized_mask > 0
                    ] = resized_ball[resized_mask > 0]
        return result_norm

    return cropped_ball

