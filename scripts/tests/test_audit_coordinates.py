"""Tests for audit_coordinates.py — pure functions only, stdlib unittest.

Run with:

    python3 -m unittest discover scripts/tests -p test_audit_coordinates.py

Tests live in scripts/tests/ alongside the existing test infrastructure
(_loader.py for PEP 723 module loading). The audit script's `requests`
import is lazy, so this test file imports the module via stdlib without
requiring `requests` to be installed.
"""

from __future__ import annotations

import unittest

from scripts.tests._loader import load_audit_coordinates


class TestHaversine(unittest.TestCase):
    def setUp(self) -> None:
        self.m = load_audit_coordinates()

    def test_paris_to_bordeaux_distance_km(self) -> None:
        # Paris (48.8566, 2.3522) → Bordeaux (44.8378, -0.5792) ≈ 506 km
        d = self.m.haversine(48.8566, 2.3522, 44.8378, -0.5792)
        self.assertAlmostEqual(d / 1000, 506, delta=10)

    def test_same_point_is_zero(self) -> None:
        d = self.m.haversine(44.8378, -0.5792, 44.8378, -0.5792)
        self.assertEqual(d, 0.0)

    def test_one_meter_north(self) -> None:
        # 1° latitude ≈ 111.32 km
        d = self.m.haversine(44.8378, -0.5792, 44.8378 + 1 / 111320, -0.5792)
        self.assertAlmostEqual(d, 1.0, delta=0.05)


class TestPointInPolygon(unittest.TestCase):
    def setUp(self) -> None:
        self.m = load_audit_coordinates()
        # Square: lon ∈ [0, 1], lat ∈ [0, 1]. GeoJSON ring: [lon, lat] pairs.
        self.square: list[list[list[float]]] = [
            [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]],
        ]
        # Square with a hole in the middle: outer ring + inner ring.
        # The hole spans lon ∈ [0.4, 0.6], lat ∈ [0.4, 0.6].
        self.square_with_hole: list[list[list[float]]] = [
            [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]],            # outer
            [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6], [0.4, 0.4]],  # hole
        ]

    def test_point_inside_square(self) -> None:
        self.assertTrue(self.m.point_in_polygon((0.5, 0.5), self.square))

    def test_point_outside_square(self) -> None:
        self.assertFalse(self.m.point_in_polygon((2.0, 2.0), self.square))

    def test_point_far_outside(self) -> None:
        self.assertFalse(self.m.point_in_polygon((-10.0, -10.0), self.square))

    def test_point_in_outer_ring_but_inside_hole(self) -> None:
        # Inside the outer ring AND inside the hole → must return False.
        # This exercises the polygon[1:] loop that production
        # geometries (Gironde reserve polygons with cutouts) need.
        self.assertFalse(self.m.point_in_polygon((0.5, 0.5), self.square_with_hole))

    def test_point_in_outer_ring_outside_hole(self) -> None:
        # Inside the outer ring but outside the hole → True.
        self.assertTrue(self.m.point_in_polygon((0.2, 0.2), self.square_with_hole))

    def test_empty_polygon_returns_false(self) -> None:
        # Defensive: an empty polygon list cannot contain any point.
        self.assertFalse(self.m.point_in_polygon((0.5, 0.5), []))


class TestSentinels(unittest.TestCase):
    def setUp(self) -> None:
        self.m = load_audit_coordinates()

    def _make_row(self, lat: str, lon: str, insee: str = "33063") -> dict[str, object]:
        return {
            "id_icpe": "test",
            "codeAiot": "0000000001",
            "raisonSociale": "TEST",
            "longitude": lon,
            "latitude": lat,
            "codeInsee": insee,
        }

    def test_null_island_origin(self) -> None:
        # The point (0, 0) is the null island sentinel.
        row = self._make_row("0", "0")
        self.m.pass_1_sentinels([row], [], {})
        self.assertIn("null_island", row["sentinel_flags"])

    def test_null_island_both_empty(self) -> None:
        # Missing coordinates count as null island for routing purposes.
        row = self._make_row("", "")
        self.m.pass_1_sentinels([row], [], {})
        self.assertIn("null_island", row["sentinel_flags"])

    def test_lon_zero_alone_is_not_null_island(self) -> None:
        # Eastern Gironde near Blaye sits on the prime meridian (lon ≈ 0.0).
        # The previous check `lat == 0 or lon == 0` over-fired here and
        # routed legitimate Gironde sites to the grand bucket without
        # geocoding. Regression guard for the behavior auditor's #2.
        row = self._make_row("44.8", "0")
        self.m.pass_1_sentinels([row], [], {})
        self.assertNotIn("null_island", row["sentinel_flags"])

    def test_lat_zero_alone_is_not_null_island(self) -> None:
        # The equator with a non-zero longitude is not the null island.
        # Symmetric guard with the lon-zero case.
        row = self._make_row("0", "-0.5")
        self.m.pass_1_sentinels([row], [], {})
        self.assertNotIn("null_island", row["sentinel_flags"])

    def test_inside_gironde_with_contour(self) -> None:
        # Tiny rectangle around Bordeaux for the test
        contour: list[list[list[list[float]]]] = [
            [[[-1.0, 44.5], [0.0, 44.5], [0.0, 45.0], [-1.0, 45.0], [-1.0, 44.5]]],
        ]
        row = self._make_row("44.8378", "-0.5792")
        self.m.pass_1_sentinels([row], contour, {})
        self.assertNotIn("outside_gironde", row["sentinel_flags"])

    def test_outside_gironde_for_paris(self) -> None:
        contour: list[list[list[list[float]]]] = [
            [[[-1.0, 44.5], [0.0, 44.5], [0.0, 45.0], [-1.0, 45.0], [-1.0, 44.5]]],
        ]
        row = self._make_row("48.8566", "2.3522")  # Paris
        self.m.pass_1_sentinels([row], contour, {})
        self.assertIn("outside_gironde", row["sentinel_flags"])

    def test_duplicate_coords_with_three_or_more(self) -> None:
        rows = [self._make_row("44.8378", "-0.5792") for _ in range(3)]
        self.m.pass_1_sentinels(rows, [], {})
        for row in rows:
            self.assertIn("duplicate_coords", row["sentinel_flags"])

    def test_duplicate_coords_with_two_does_not_flag(self) -> None:
        rows = [self._make_row("44.8378", "-0.5792") for _ in range(2)]
        self.m.pass_1_sentinels(rows, [], {})
        for row in rows:
            self.assertNotIn("duplicate_coords", row["sentinel_flags"])

    def test_commune_centroid_within_radius_flags(self) -> None:
        # A row sitting at the centroid of its declared commune polygon
        # should be flagged as commune_centroid (the geocoder-fallback
        # signature). This branch was previously untested because every
        # other test passed an empty commune_polys dict.
        # Tiny square polygon centered at (44.8, -0.5).
        polys = {
            "33063": [
                [
                    [
                        [-0.501, 44.799],
                        [-0.499, 44.799],
                        [-0.499, 44.801],
                        [-0.501, 44.801],
                        [-0.501, 44.799],
                    ],
                ],
            ],
        }
        row = self._make_row("44.8", "-0.5", insee="33063")  # exactly at centroid
        self.m.pass_1_sentinels([row], [], polys)
        self.assertIn("commune_centroid", row["sentinel_flags"])

    def test_commune_centroid_far_from_centroid_does_not_flag(self) -> None:
        polys = {
            "33063": [
                [
                    [
                        [-0.501, 44.799],
                        [-0.499, 44.799],
                        [-0.499, 44.801],
                        [-0.501, 44.801],
                        [-0.501, 44.799],
                    ],
                ],
            ],
        }
        # Same INSEE but a stored point far from the centroid (>50 m away).
        row = self._make_row("44.85", "-0.55", insee="33063")
        self.m.pass_1_sentinels([row], [], polys)
        self.assertNotIn("commune_centroid", row["sentinel_flags"])


class TestClassification(unittest.TestCase):
    def setUp(self) -> None:
        self.m = load_audit_coordinates()
        self.thresholds = self.m.DEFAULT_THRESHOLDS

    def _row(self, **kwargs: object) -> dict[str, object]:
        base: dict[str, object] = {
            "sentinel_flags": "",
            "pip_in_commune": True,
            "reverse_citycode": "",
            "codeInsee": "33063",
            "forward_score": 0.95,
            "forward_type": "housenumber",
            "forward_distance_m": 0.0,
            "forward_error": "",
        }
        base.update(kwargs)
        return base

    def test_null_island_wins(self) -> None:
        row = self._row(sentinel_flags="null_island", forward_distance_m=10000)
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.NULL_ISLAND)

    def test_outside_gironde(self) -> None:
        row = self._row(sentinel_flags="outside_gironde")
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.OUTSIDE_GIRONDE)

    def test_wrong_commune_via_pip(self) -> None:
        row = self._row(pip_in_commune=False)
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.WRONG_COMMUNE)

    def test_wrong_commune_via_reverse_mismatch(self) -> None:
        row = self._row(reverse_citycode="33099")  # different from declared 33063
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.WRONG_COMMUNE)

    def test_address_unresolvable_isolated_no_reverse(self) -> None:
        row = self._row(forward_score=0.2, reverse_citycode="")
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.ADDRESS_UNRESOLVABLE_ISOLATED)

    def test_address_unresolvable_isolated_reverse_mismatch_actually_caught_as_wrong_commune(self) -> None:
        # If reverse_citycode mismatches, the wrong_commune check fires FIRST
        row = self._row(forward_score=0.2, reverse_citycode="33099")
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.WRONG_COMMUNE)

    def test_address_unresolvable_commune_ok_via_reverse(self) -> None:
        # Forward fails but reverse_citycode confirms declared codeInsee
        row = self._row(forward_score=0.2, reverse_citycode="33063")  # same as declared
        self.assertEqual(
            self.m.classify(row, self.thresholds),
            self.m.AuditClass.ADDRESS_UNRESOLVABLE_COMMUNE_OK,
        )

    def test_address_unresolvable_no_score(self) -> None:
        row = self._row(forward_score=None, reverse_citycode="")
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.ADDRESS_UNRESOLVABLE_ISOLATED)

    def test_address_imprecise(self) -> None:
        row = self._row(forward_type="locality")
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.ADDRESS_IMPRECISE)

    def test_very_severe_distance(self) -> None:
        row = self._row(forward_distance_m=3000)
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.VERY_SEVERE)

    def test_severe_distance(self) -> None:
        row = self._row(forward_distance_m=600)
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.SEVERE)

    def test_suspicious_distance(self) -> None:
        row = self._row(forward_distance_m=200)
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.SUSPICIOUS)

    def test_minor_distance(self) -> None:
        row = self._row(forward_distance_m=50)
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.MINOR)

    def test_ok_below_threshold(self) -> None:
        row = self._row(forward_distance_m=10)
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.OK)

    def test_forward_distance_none_is_ok(self) -> None:
        # None must not crash; defaults to OK if no other signal triggered
        row = self._row(forward_distance_m=None)
        # forward_score is still 0.95 and type is housenumber, so distance None → ok
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.OK)

    # ---- Ladder priority interaction tests ------------------------------
    # The classify() function is a first-match-wins ladder. These tests
    # encode the priority order so a future refactor that swaps two checks
    # cannot pass silently.

    def test_outside_gironde_wins_over_wrong_commune(self) -> None:
        # Both signals fire: outside_gironde sentinel AND pip_in_commune=False.
        # The ladder must return OUTSIDE_GIRONDE (sentinel) not WRONG_COMMUNE.
        row = self._row(sentinel_flags="outside_gironde", pip_in_commune=False)
        self.assertEqual(
            self.m.classify(row, self.thresholds),
            self.m.AuditClass.OUTSIDE_GIRONDE,
        )

    def test_address_imprecise_wins_over_distance_ladder(self) -> None:
        # forward_type=locality (imprecise) AND a distance that would
        # otherwise trigger VERY_SEVERE. address_imprecise must win.
        row = self._row(forward_type="locality", forward_distance_m=3000)
        self.assertEqual(
            self.m.classify(row, self.thresholds),
            self.m.AuditClass.ADDRESS_IMPRECISE,
        )

    def test_wrong_commune_wins_over_distance(self) -> None:
        # pip_in_commune=False AND a distance below all thresholds.
        # WRONG_COMMUNE must take precedence over OK.
        row = self._row(pip_in_commune=False, forward_distance_m=10)
        self.assertEqual(
            self.m.classify(row, self.thresholds),
            self.m.AuditClass.WRONG_COMMUNE,
        )

    # ---- Distance threshold boundary tests -------------------------------
    # Defaults: very_severe ≥ 2000, severe ≥ 500, suspicious ≥ 100, minor ≥ 25.
    # Probe each boundary at the exact value and one below.

    def test_distance_at_very_severe_threshold(self) -> None:
        row = self._row(forward_distance_m=2000)
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.VERY_SEVERE)

    def test_distance_just_below_very_severe(self) -> None:
        row = self._row(forward_distance_m=1999)
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.SEVERE)

    def test_distance_at_severe_threshold(self) -> None:
        row = self._row(forward_distance_m=500)
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.SEVERE)

    def test_distance_just_below_severe(self) -> None:
        row = self._row(forward_distance_m=499)
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.SUSPICIOUS)

    def test_distance_at_suspicious_threshold(self) -> None:
        row = self._row(forward_distance_m=100)
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.SUSPICIOUS)

    def test_distance_just_below_suspicious(self) -> None:
        row = self._row(forward_distance_m=99)
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.MINOR)

    def test_distance_at_minor_threshold(self) -> None:
        row = self._row(forward_distance_m=25)
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.MINOR)

    def test_distance_just_below_minor(self) -> None:
        row = self._row(forward_distance_m=24)
        self.assertEqual(self.m.classify(row, self.thresholds), self.m.AuditClass.OK)


class TestGroupAssignment(unittest.TestCase):
    def setUp(self) -> None:
        self.m = load_audit_coordinates()

    def test_reserve_ambiguous_wins_over_severe(self) -> None:
        row = {
            "audit_class": self.m.AuditClass.SEVERE.value,
            "reserve_ambiguous": True,
            "reserve_boundary_proximity": False,
            "forward_distance_m": 1000,
        }
        self.assertEqual(self.m.assign_group(row), self.m.AuditGroup.RESERVES)

    def test_boundary_proximity_with_distance_above_25_to_reserves(self) -> None:
        row = {
            "audit_class": self.m.AuditClass.MINOR.value,
            "reserve_ambiguous": False,
            "reserve_boundary_proximity": True,
            "forward_distance_m": 50,
        }
        self.assertEqual(self.m.assign_group(row), self.m.AuditGroup.RESERVES)

    def test_boundary_proximity_with_distance_below_25_stays_in_distance_group(self) -> None:
        row = {
            "audit_class": self.m.AuditClass.OK.value,
            "reserve_ambiguous": False,
            "reserve_boundary_proximity": True,
            "forward_distance_m": 10,
        }
        # forward_distance_m == 10, not > 25, so doesn't go to reserves group
        # And class is ok → no group
        self.assertIsNone(self.m.assign_group(row))

    def test_boundary_proximity_with_null_distance_does_not_crash(self) -> None:
        # The critical None-safety test (DD #7)
        row = {
            "audit_class": self.m.AuditClass.NULL_ISLAND.value,
            "reserve_ambiguous": False,
            "reserve_boundary_proximity": True,
            "forward_distance_m": None,
        }
        result = self.m.assign_group(row)
        # null_island goes to grand (not reserves, because distance is None)
        self.assertEqual(result, self.m.AuditGroup.GRAND)

    def test_severe_class_to_grand(self) -> None:
        row = {
            "audit_class": self.m.AuditClass.SEVERE.value,
            "reserve_ambiguous": False,
            "reserve_boundary_proximity": False,
            "forward_distance_m": 800,
        }
        self.assertEqual(self.m.assign_group(row), self.m.AuditGroup.GRAND)

    def test_suspicious_class_to_petit(self) -> None:
        row = {
            "audit_class": self.m.AuditClass.SUSPICIOUS.value,
            "reserve_ambiguous": False,
            "reserve_boundary_proximity": False,
            "forward_distance_m": 200,
        }
        self.assertEqual(self.m.assign_group(row), self.m.AuditGroup.PETIT)

    def test_address_unresolvable_commune_ok_to_petit(self) -> None:
        # Forward failed but reverse confirms commune → petit (low priority)
        row = {
            "audit_class": self.m.AuditClass.ADDRESS_UNRESOLVABLE_COMMUNE_OK.value,
            "reserve_ambiguous": False,
            "reserve_boundary_proximity": False,
            "forward_distance_m": None,
        }
        self.assertEqual(self.m.assign_group(row), self.m.AuditGroup.PETIT)

    def test_address_unresolvable_isolated_to_grand(self) -> None:
        row = {
            "audit_class": self.m.AuditClass.ADDRESS_UNRESOLVABLE_ISOLATED.value,
            "reserve_ambiguous": False,
            "reserve_boundary_proximity": False,
            "forward_distance_m": None,
        }
        self.assertEqual(self.m.assign_group(row), self.m.AuditGroup.GRAND)

    def test_ok_class_no_group(self) -> None:
        row = {
            "audit_class": self.m.AuditClass.OK.value,
            "reserve_ambiguous": False,
            "reserve_boundary_proximity": False,
            "forward_distance_m": 10,
        }
        self.assertIsNone(self.m.assign_group(row))

    # ---- Full AuditClass → AuditGroup coverage --------------------------
    # The previous coverage missed VERY_SEVERE, OUTSIDE_GIRONDE,
    # WRONG_COMMUNE, ADDRESS_IMPRECISE, and MINOR. Each gets an explicit
    # routing assertion so a future swap of any single mapping fails.

    def _grand_row(self, cls) -> dict:
        return {
            "audit_class": cls,
            "reserve_ambiguous": False,
            "reserve_boundary_proximity": False,
            "forward_distance_m": 1000,
        }

    def test_very_severe_routes_to_grand(self) -> None:
        self.assertEqual(
            self.m.assign_group(self._grand_row(self.m.AuditClass.VERY_SEVERE)),
            self.m.AuditGroup.GRAND,
        )

    def test_outside_gironde_routes_to_grand(self) -> None:
        self.assertEqual(
            self.m.assign_group(self._grand_row(self.m.AuditClass.OUTSIDE_GIRONDE)),
            self.m.AuditGroup.GRAND,
        )

    def test_wrong_commune_routes_to_grand(self) -> None:
        self.assertEqual(
            self.m.assign_group(self._grand_row(self.m.AuditClass.WRONG_COMMUNE)),
            self.m.AuditGroup.GRAND,
        )

    def test_address_imprecise_routes_to_grand(self) -> None:
        row = {
            "audit_class": self.m.AuditClass.ADDRESS_IMPRECISE,
            "reserve_ambiguous": False,
            "reserve_boundary_proximity": False,
            "forward_distance_m": 200,
        }
        # ADDRESS_IMPRECISE is in the `grand` set — when the geocoder
        # only resolves to the locality level, the reviewer needs the
        # full review treatment.
        self.assertEqual(self.m.assign_group(row), self.m.AuditGroup.GRAND)

    def test_minor_class_routes_to_petit(self) -> None:
        row = {
            "audit_class": self.m.AuditClass.MINOR,
            "reserve_ambiguous": False,
            "reserve_boundary_proximity": False,
            "forward_distance_m": 50,
        }
        self.assertEqual(self.m.assign_group(row), self.m.AuditGroup.PETIT)


class TestFlaggedHash(unittest.TestCase):
    def setUp(self) -> None:
        self.m = load_audit_coordinates()

    def test_same_input_produces_same_hash(self) -> None:
        items = [{"id_icpe": "100"}, {"id_icpe": "200"}]
        h1 = self.m.build_flagged_hash(items)
        h2 = self.m.build_flagged_hash(items)
        self.assertEqual(h1, h2)
        self.assertTrue(h1.startswith("sha256:"))

    def test_reordered_input_produces_same_hash(self) -> None:
        items_a = [{"id_icpe": "100"}, {"id_icpe": "200"}, {"id_icpe": "300"}]
        items_b = [{"id_icpe": "300"}, {"id_icpe": "100"}, {"id_icpe": "200"}]
        self.assertEqual(self.m.build_flagged_hash(items_a), self.m.build_flagged_hash(items_b))

    def test_different_items_produce_different_hash(self) -> None:
        items_a = [{"id_icpe": "100"}, {"id_icpe": "200"}]
        items_b = [{"id_icpe": "100"}, {"id_icpe": "201"}]
        self.assertNotEqual(self.m.build_flagged_hash(items_a), self.m.build_flagged_hash(items_b))


class TestMetadataSelfConsistency(unittest.TestCase):
    def setUp(self) -> None:
        self.m = load_audit_coordinates()

    def test_audit_owned_columns_match_metadata_rows(self) -> None:
        """The internal drift guard should pass for the actual constants."""
        # Should NOT raise
        self.m.assert_metadata_self_consistent(self.m.AUDIT_OWNED_COLUMNS)

    def test_drift_guard_catches_missing_metadata(self) -> None:
        # Add a fake column not in AUDIT_METADATA_ROWS
        cols = self.m.AUDIT_OWNED_COLUMNS | {"fake_new_column"}
        with self.assertRaises(AssertionError):
            self.m.assert_metadata_self_consistent(cols)

    def test_drift_guard_catches_extra_metadata(self) -> None:
        cols = self.m.AUDIT_OWNED_COLUMNS - {"audit_class"}
        with self.assertRaises(AssertionError):
            self.m.assert_metadata_self_consistent(cols)


class TestBANResponseParsing(unittest.TestCase):
    def setUp(self) -> None:
        self.m = load_audit_coordinates()

    def test_parse_simple_response(self) -> None:
        csv_bytes = (
            b"id_icpe,adresse,longitude,latitude,result_score,result_type,result_label,result_status\n"
            b"100,15 cours Pasteur,-0.5774,44.8338,0.97,housenumber,15 Cours Pasteur 33000 Bordeaux,ok\n"
        )
        parsed = self.m.parse_ban_response(csv_bytes)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["id_icpe"], "100")
        self.assertEqual(parsed[0]["result_status"], "ok")
        self.assertEqual(parsed[0]["longitude"], "-0.5774")

    def test_parse_empty_response(self) -> None:
        csv_bytes = b"id_icpe,adresse,longitude,latitude,result_score\n"
        parsed = self.m.parse_ban_response(csv_bytes)
        self.assertEqual(parsed, [])


class TestParseFloat(unittest.TestCase):
    def setUp(self) -> None:
        self.m = load_audit_coordinates()

    def test_valid_float(self) -> None:
        self.assertEqual(self.m.parse_float("44.8378"), 44.8378)

    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(self.m.parse_float(""))

    def test_invalid_returns_none(self) -> None:
        self.assertIsNone(self.m.parse_float("not a number"))

    def test_negative(self) -> None:
        self.assertEqual(self.m.parse_float("-1.234"), -1.234)

    def test_french_decimal_comma(self) -> None:
        # Behavior #3 regression guard: a French-locale float should
        # parse as the equivalent dot-formatted value, not return None
        # (which would route the row to null-island).
        self.assertEqual(self.m.parse_float("44,8378"), 44.8378)
        self.assertEqual(self.m.parse_float("-0,5792"), -0.5792)

    def test_thousands_separator_rejected(self) -> None:
        # Ambiguous: "1,234" could be French 1.234 or English 1234.
        # parse_float should return None for multi-comma OR comma-with-dot
        # rather than guessing.
        self.assertIsNone(self.m.parse_float("1,234.5"))
        self.assertIsNone(self.m.parse_float("1,234,567"))


class TestAuditRunIdDeterminism(unittest.TestCase):
    """Behavior #1 regression guard: flagged.json must be byte-stable
    across re-runs with the same inputs. The audit_run_id is now derived
    from flagged_hash, which is itself a sha256 of the sorted id_icpe set.
    Two runs with the same flagged set must produce the same audit_run_id.
    """

    def setUp(self) -> None:
        self.m = load_audit_coordinates()

    def test_same_items_yield_same_hash(self) -> None:
        items_a = [{"id_icpe": "100"}, {"id_icpe": "200"}, {"id_icpe": "300"}]
        items_b = [{"id_icpe": "300"}, {"id_icpe": "200"}, {"id_icpe": "100"}]
        # Sort-invariant hash means audit_run_id is order-independent.
        self.assertEqual(
            self.m.build_flagged_hash(items_a),
            self.m.build_flagged_hash(items_b),
        )

    def test_different_items_yield_different_hash(self) -> None:
        items_a = [{"id_icpe": "100"}, {"id_icpe": "200"}]
        items_b = [{"id_icpe": "100"}, {"id_icpe": "201"}]
        self.assertNotEqual(
            self.m.build_flagged_hash(items_a),
            self.m.build_flagged_hash(items_b),
        )


class TestNormalizeRegimeAndSeveso(unittest.TestCase):
    """Behavior #7 regression guards for the regime/seveso passthrough fix.
    These live in enrichir_libelles.py rather than audit_coordinates.py;
    we import via importlib because enrichir_libelles is also a script.
    """

    def setUp(self) -> None:
        import importlib.util
        import sys
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "enrichir_libelles_test_load",
            Path(__file__).resolve().parents[2] / "scripts" / "enrichir_libelles.py",
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules["enrichir_libelles_test_load"] = mod
        spec.loader.exec_module(mod)
        self.m = mod

    def test_known_regime_maps_correctly(self) -> None:
        self.assertEqual(self.m._normalize_regime("Autorisation"), "AUTORISATION")
        self.assertEqual(self.m._normalize_regime("Enregistrement"), "ENREGISTREMENT")
        self.assertEqual(self.m._normalize_regime("Autres régimes"), "AUTRE")
        self.assertEqual(self.m._normalize_regime("Non ICPE"), "NON_ICPE")

    def test_unknown_regime_falls_back_to_AUTRE(self) -> None:
        # The previous .get(val, val) silently leaked the raw string.
        # The new helper falls back to "AUTRE" with a stderr warning.
        self.assertEqual(
            self.m._normalize_regime("Déclaration avec contrôle"),
            "AUTRE",
        )

    def test_known_seveso_maps_correctly(self) -> None:
        self.assertEqual(self.m._normalize_seveso(""), "")
        self.assertEqual(self.m._normalize_seveso("Non Seveso"), "NON_SEVESO")
        self.assertEqual(self.m._normalize_seveso("Seveso seuil bas"), "SEUIL_BAS")
        self.assertEqual(self.m._normalize_seveso("Seveso seuil haut"), "SEUIL_HAUT")

    def test_unknown_seveso_falls_back_to_NON_SEVESO(self) -> None:
        self.assertEqual(
            self.m._normalize_seveso("Seveso seuil ultra haut"),
            "NON_SEVESO",
        )


if __name__ == "__main__":
    unittest.main()
