"""Tests for :mod:`alveoslicer.core.geometry`, :mod:`alveoslicer.core.mesh_ops`, and :mod:`alveoslicer.core.scene`."""

from __future__ import annotations

import subprocess
import sys
import unittest

import numpy as np
import trimesh
from shapely.geometry import Polygon

from alveoslicer import (
    SlicerConfig,
    WorkspaceScene,
    compute_placeable_faces,
    create_test_curved_wall_mesh,
    create_test_sphere_mesh,
    cut_mesh_by_plane,
)
from alveoslicer.core.geometry import (
    clean_geom,
    safe_buffer,
    safe_difference,
    safe_intersection,
    safe_union,
)
from alveoslicer.slicing.supports import generate_dry_core_supports


class TestGeometryAndScene(unittest.TestCase):
    """Verify watertight geometry helpers, headless isolation, plane cuts, and multi-plate scene."""

    def test_builtin_meshes_are_watertight_and_grounded(self) -> None:
        cfg = SlicerConfig()
        for builder in (create_test_sphere_mesh, create_test_curved_wall_mesh):
            mesh = builder(cfg)
            self.assertTrue(mesh.is_watertight, f"{builder.__name__} must be watertight")
            self.assertAlmostEqual(float(mesh.bounds[0, 2]), 0.0, places=5)
            self.assertGreater(mesh.volume, 100.0)

    def test_headless_core_does_not_import_gui_libs(self) -> None:
        code = (
            "import sys, alveoslicer; "
            "assert 'PySide6' not in sys.modules; "
            "assert 'matplotlib' not in sys.modules"
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_multi_plate_instances_arrange_and_place_on_face(self) -> None:
        scene = WorkspaceScene()
        box = trimesh.creation.box(extents=[24.0, 16.0, 12.0])

        inst1 = scene.add_mesh(box, "Bracket", 220.0, 220.0)
        inst2 = scene.duplicate_selected(220.0, 220.0)
        self.assertIsNotNone(inst2)
        self.assertEqual(len(scene.active_plate.instances), 2)

        scene.active_plate.auto_arrange(220.0, 220.0, spacing=8.0)
        b1 = inst1.transformed_mesh().bounds
        b2 = inst2.transformed_mesh().bounds
        sep_x = (b1[1, 0] < b2[0, 0]) or (b2[1, 0] < b1[0, 0])
        sep_y = (b1[1, 1] < b2[0, 1]) or (b2[1, 1] < b1[0, 1])
        self.assertTrue(sep_x or sep_y, "Arranged instances must not overlap in XY")

        plate2 = scene.add_plate()
        self.assertEqual(len(scene.plates), 2)
        self.assertEqual(plate2.name, "Plate 2")
        scene.set_active_plate(0)
        scene.selected_instance_id = inst2.instance_id
        self.assertTrue(scene.move_selected_to_plate(1, 220.0, 220.0))
        self.assertEqual(len(scene.plates[0].instances), 1)
        self.assertEqual(len(scene.plates[1].instances), 1)

        inst1.rot_x = 37.0
        inst1.rot_y = 22.0
        inst1.pos_z = 5.0
        inst1.invalidate()
        faces = compute_placeable_faces(inst1.transformed_mesh())
        self.assertEqual(len(faces), 6, "A box should cluster into exactly 6 simplified landing faces")

        target_face = max(faces, key=lambda f: abs(f.normal[0]) + abs(f.normal[1]))
        inst1.align_normal_to_bed(target_face.normal)
        aligned_mesh = inst1.transformed_mesh()
        self.assertAlmostEqual(float(aligned_mesh.bounds[0, 2]), 0.0, places=4)
        z_ext = float(aligned_mesh.extents[2])
        min_err = min(abs(z_ext - d) for d in (12.0, 16.0, 24.0))
        self.assertLess(min_err, 0.15, f"Expected aligned box height in (12, 16, 24), got {z_ext}")

    def test_topology_exception_resilience_on_invalid_self_intersecting_polygons(self) -> None:
        bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10), (0, 0)])
        self.assertFalse(bowtie.is_valid)
        cleaned = clean_geom(bowtie)
        self.assertTrue(cleaned.is_valid)
        self.assertGreater(cleaned.area, 0.0)

        b2 = Polygon([(2, 0), (12, 10), (12, 0), (2, 10), (2, 0)])
        self.assertTrue(safe_union([bowtie, b2]).is_valid)
        self.assertTrue(safe_intersection(bowtie, b2).is_valid)
        self.assertTrue(safe_difference(bowtie, b2).is_valid)
        self.assertTrue(safe_buffer(bowtie, 0.4).is_valid)

        dry_cores = [bowtie, bowtie, Polygon()]
        inner_skins = [Polygon(), Polygon(), Polygon([(0, 0), (12, 0), (12, 12), (0, 12)])]
        lines_all, polys_all = generate_dry_core_supports(dry_cores, inner_skins, lw=0.42, spacing_mm=2.0)
        self.assertEqual(len(lines_all), 3)
        self.assertEqual(len(polys_all), 3)
        self.assertGreater(len(lines_all[0]), 0)
        self.assertGreater(len(lines_all[1]), 0)

    def test_watertight_3d_plane_cut_and_scene_cut(self) -> None:
        cfg = SlicerConfig()
        sphere = create_test_sphere_mesh(cfg, radius=14.0)
        orig_vol = float(sphere.volume)
        center = 0.5 * (sphere.bounds[0] + sphere.bounds[1])

        normal = np.array([0.25, -0.15, 1.0], dtype=np.float64)
        upper, lower = cut_mesh_by_plane(sphere, center, normal, keep_upper=True, keep_lower=True)
        self.assertIsNotNone(upper)
        self.assertIsNotNone(lower)
        self.assertTrue(upper.is_watertight, "Upper cut half must be watertight")
        self.assertTrue(lower.is_watertight, "Lower cut half must be watertight")
        self.assertAlmostEqual(
            float(upper.volume + lower.volume) / orig_vol,
            1.0,
            delta=0.05,
            msg="Cut halves must preserve total volume",
        )

        scene = WorkspaceScene()
        scene.add_mesh(sphere, "Sphere", 220.0, 220.0)
        created = scene.cut_selected_instance(
            plane_origin=center,
            plane_normal=np.array([0.0, 0.0, 1.0]),
            bed_x=220.0,
            bed_y=220.0,
            keep_upper=True,
            keep_lower=True,
            place_upper_on_bed=True,
            place_lower_on_bed=False,
        )
        self.assertEqual(len(created), 2)
        self.assertEqual(len(scene.active_plate.instances), 2)
        for inst in created:
            m = inst.transformed_mesh()
            self.assertAlmostEqual(float(m.bounds[0, 2]), 0.0, places=3)


if __name__ == "__main__":
    unittest.main()
