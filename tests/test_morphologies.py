import unittest, os, sys, numpy as np, h5py
import json
import itertools

from bsb.services import MPI
from bsb.morphologies import Morphology, Branch, _Labels, MorphologySet
from bsb.storage import Storage
from bsb.storage.interfaces import StoredMorphology
from bsb.exceptions import *
from bsb.unittest import get_morphology_path, NumpyTestCase
from scipy.spatial.transform import Rotation


class TestIO(NumpyTestCase, unittest.TestCase):
    def test_swc_2comp(self):
        m = Morphology.from_swc(get_morphology_path("2comp.swc"))
        self.assertEqual(2, len(m), "Expected 2 points on the morphology")
        self.assertEqual(1, len(m.roots), "Expected 1 root on the morphology")
        self.assertClose([1, 1], m.tags, "tags should be all soma")
        self.assertClose(1, m.labels, "labels should be all soma")
        self.assertEqual({0: set(), 1: {"soma"}}, m.labels.labels, "incorrect labelsets")

    def test_swc_2root(self):
        m = Morphology.from_swc(get_morphology_path("2root.swc"))
        self.assertEqual(2, len(m), "Expected 2 points on the morphology")
        self.assertEqual(2, len(m.roots), "Expected 2 roots on the morphology")

    def test_swc_branch_filling(self):
        m = Morphology.from_swc(get_morphology_path("3branch.swc"))
        # SWC specifies child-parent edges, when translating that to branches, at branch
        # points some points need to be duplicated: there's 4 samples (SWC) and 2 child
        # branches -> 2 extra points == 6 points
        self.assertEqual(6, len(m), "Expected 6 points on the morphology")
        self.assertEqual(3, len(m.branches), "Expected 3 branches on the morphology")
        self.assertEqual(1, len(m.roots), "Expected 1 root on the morphology")

    def test_known(self):
        # TODO: Check the morphos visually with glover
        m = Morphology.from_swc(get_morphology_path("PurkinjeCell.swc"))
        self.assertEqual(3834, len(m), "Amount of point on purkinje changed")
        self.assertEqual(459, len(m.branches), "Amount of branches on purkinje changed")
        self.assertEqual(
            42.45157433053635,
            np.mean(m.points),
            "value of the universe, life and everything changed.",
        )
        m = Morphology.from_file(get_morphology_path("GolgiCell.asc"))
        self.assertEqual(5105, len(m), "Amount of point on purkinje changed")
        self.assertEqual(227, len(m.branches), "Amount of branches on purkinje changed")
        self.assertEqual(
            -11.14412080401295,
            np.mean(m.points),
            "something in the points changed.",
        )

    def test_shared_labels(self):
        m = Morphology.from_swc(get_morphology_path("PurkinjeCell.swc"))
        m2 = Morphology.from_swc(get_morphology_path("PurkinjeCell.swc"))
        l = m._shared._labels.labels
        self.assertIsNot(l, m2._shared._labels.label, "reload shares state")
        for b in m.branches:
            self.assertTrue(l is b._labels.labels, "Labels should be shared")
            l = b._labels.labels


class TestMorphologies(NumpyTestCase, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    def _branch(self, len):
        return Branch(np.ones((len, 3)), np.ones(len), _Labels.none(len), {})

    def test_branch_attachment(self):
        branch_A = self._branch(5)
        branch_B = self._branch(5)
        branch_C = self._branch(5)
        branch_D = self._branch(5)
        branch_A.attach_child(branch_B)
        branch_A.attach_child(branch_C)
        branch_B.attach_child(branch_D)
        self.assertEqual([branch_B, branch_C], branch_A._children)
        self.assertFalse(branch_A.is_terminal)
        self.assertFalse(branch_B.is_terminal)
        self.assertTrue(branch_C.is_terminal)
        self.assertTrue(branch_D.is_terminal)
        branch_A.detach_child(branch_C)
        self.assertIsNone(branch_C._parent)
        with self.assertRaises(ValueError):
            branch_A.detach_child(branch_D)
        self.assertEqual(branch_B, branch_D._parent)

    def test_properties(self):
        branch = Branch(
            np.array(
                [
                    [0, 1, 2],
                    [0, 1, 2],
                    [0, 1, 2],
                ]
            ),
            np.array([0, 1, 2]),
        )
        self.assertEqual(3, branch.size, "Incorrect branch size")
        self.assertTrue(branch.is_terminal)
        branch.attach_child(branch)
        self.assertFalse(branch.is_terminal)

    def test_optimize(self):
        b1 = self._branch(3)
        b1.set_properties(smth=np.ones(len(b1)))
        b2 = self._branch(3)
        b2.label("oy")
        b2.translate([100, 100, 100])
        b2.set_properties(other=np.zeros(len(b2)), smth=np.ones(len(b2)))
        b3 = self._branch(3)
        b3.translate([200, 200, 200])
        b3.label("vey")
        b3.set_properties(other=np.ones(len(b3)))
        b4 = self._branch(3)
        b4.label("oy", "vey")
        b5 = self._branch(3)
        b5.label("oy")
        b5.translate([100, 100, 100])
        b6 = self._branch(3)
        b6.translate([200, 200, 200])
        b6.label("vey", "oy")
        m = Morphology([b1, b2, b3, b4, b5, b6])
        m.optimize()
        self.assertTrue(m._is_shared, "Should be shared after opt")
        self.assertEqual(18, len(m), "opt changed n points")
        self.assertClose(
            np.array([[1, 1, 1, 101, 101, 101, 201, 201, 201] * 2] * 3).T, m.points
        )
        self.assertClose(
            [0, 0, 0, 1, 1, 1, 3, 3, 3, 2, 2, 2, 1, 1, 1, 2, 2, 2],
            m._shared._labels,
        )
        self.assertClose(1, m.smth[:6], "prop concat failed")
        self.assertNan(m.smth[6:], "prop concat failed")
        self.assertClose(0, m.other[3:6], "prop concat failed")
        self.assertClose(1, m.other[6:9], "prop concat failed")
        self.assertNan(m.other[9:], "prop concat failed")
        self.assertNan(m.other[:3], "prop concat failed")
        # Test DFS reorder of opt
        b1.attach_child(b3)
        m.roots.remove(b3)
        b4.attach_child(b6)
        m.roots.remove(b6)
        m.optimize(force=True)
        self.assertClose(
            np.array([[1, 1, 1, 201, 201, 201, 101, 101, 101] * 2] * 3).T, m.points
        )
        # Compare opt to flatten
        self.assertEqual(
            m.other[3:9].tolist(), m.flatten_properties()["other"][3:9].tolist()
        )
        l1 = m._shared._labels
        l2 = m.flatten_labels()
        self.assertClose(l1, l2, "opt v flatten labels discrepancy")
        self.assertEqual(l1.labels, l2.labels, "opt v flatten labels discrepancy")

    def test_chaining(self):
        branch = Branch(
            np.array(
                [
                    [0, 1, 2],
                    [0, 1, 2],
                    [0, 1, 2],
                ]
            ),
            np.array([0, 1, 2]),
        )
        m = Morphology([branch])
        r = Rotation.from_euler("z", 0)
        res = m.rotate(r).root_rotate(r).translate([0, 0, 0]).collapse().close_gaps()
        self.assertEqual(m, res, "chaining calls should return self")


class TestMorphologyLabels(NumpyTestCase, unittest.TestCase):
    def test_labels(self):
        a = _Labels.none(10)
        self.assertEqual({0: set()}, a.labels, "none labels should be empty")
        self.assertClose(0, a, "none labels should zero")
        a.label(["ello"], [1, 2])
        self.assertEqual({0: set(), 1: {"ello"}}, a.labels)
        self.assertClose([0, 1, 1, 0, 0, 0, 0, 0, 0, 0], a)
        a.label(["ello", "goodbye"], [1, 2, 3, 4])
        self.assertEqual({0: set(), 1: {"ello"}, 2: {"ello", "goodbye"}}, a.labels)
        self.assertClose([0, 2, 2, 2, 2, 0, 0, 0, 0, 0], a)
        a.label(["goodbye"], [5, 6])
        self.assertEqual(
            {0: set(), 1: {"ello"}, 2: {"ello", "goodbye"}, 3: {"goodbye"}}, a.labels
        )
        self.assertClose([0, 2, 2, 2, 2, 3, 3, 0, 0, 0], a)
        a.label(["ello"], [9])
        self.assertEqual(
            {0: set(), 1: {"ello"}, 2: {"ello", "goodbye"}, 3: {"goodbye"}}, a.labels
        )
        self.assertClose([0, 2, 2, 2, 2, 3, 3, 0, 0, 1], a)
        a.label(["ello"], [*range(10)])
        self.assertClose([1, 2, 2, 2, 2, 2, 2, 1, 1, 1], a)
        a.label(["goodbye"], [*range(10)])
        self.assertClose([2] * 10, a)

    def test_branch_labels(self):
        b = Branch([[0] * 3] * 10, [1] * 10)
        a = b._labels
        self.assertEqual({0: set()}, a.labels, "none labels should be empty")
        self.assertClose(0, a, "none labels should zero")
        b.label("ello")
        self.assertClose(1, a, "full labelling failed")
        b.label("so long", "goodbye", "sayonara")
        self.assertClose(2, a, "multifull labelling failed")
        self.assertEqual(
            {0: set(), 1: {"ello"}, 2: {"ello", "so long", "goodbye", "sayonara"}},
            a.labels,
        )
        b.label([1, 3], "wow")
        self.assertClose([2, 3, 2, 3, 2, 2, 2, 2, 2, 2], a, "specific point label failed")

    def test_copy_labels(self):
        b = Branch([[0] * 3] * 10, [1] * 10)
        b.label("ello")
        b.label("so long", "goodbye", "sayonara")
        b.label([1, 3], "wow")
        b2 = b.copy()
        self.assertEqual(len(b), len(b2), "copy changed n points")
        self.assertEqual(b._labels.labels, b2._labels.labels, "copy changed labelset")
        self.assertIsNot(b._labels.labels, b2._labels.labels, "copy shares labels")

    def test_concat(self):
        b = Branch([[0] * 3] * 10, [1] * 10)
        b.label("ello")
        b2 = Branch([[0] * 3] * 10, [1] * 10)
        b2.label("not ello")
        # Both branches have a different definition for `1`, so concat should map them.
        self.assertClose(1, b._labels, "should all be labelled to 1")
        self.assertClose(1, b2._labels, "should all be labelled to 1")
        self.assertNotEqual(b._labels.labels, b2._labels.labels, "should have diff def")
        concat = _Labels.concatenate(b._labels, b2._labels)
        self.assertClose([1] * 10 + [2] * 10, concat)
        self.assertEqual({0: set(), 1: {"ello"}, 2: {"not ello"}}, concat.labels)

    def test_select(self):
        b = Branch([[0] * 3] * 10, [1] * 10)
        b.name = "B1"
        b.label("ello")
        b2 = Branch([[0] * 3] * 10, [1] * 10)
        b2.name = "B2"
        b3 = Branch([[0] * 3] * 10, [1] * 10)
        b3.name = "B3"
        b4 = Branch([[0] * 3] * 10, [1] * 10)
        b4.name = "B4"
        b3.attach_child(b4)
        b3.label([1], "ello")
        self.assertTrue(b3.contains_label("ello"))
        m = Morphology([b, b2, b3])
        bs = m.subtree("ello").branches
        self.assertEqual([b, b3, b4], m.subtree("ello").branches)
        self.assertEqual(len(b), len(b.get_points_labelled("ello")))
        self.assertEqual(1, len(b3.get_points_labelled("ello")))


class TestMorphologySet(unittest.TestCase):
    def _fake_loader(self, name):
        return StoredMorphology(name, lambda: Morphology([Branch([], [])]), dict())

    def setUp(self):
        self.sets = [
            MorphologySet([], []),
            MorphologySet([self._fake_loader("ello")], [0, 0, 0]),
        ]

    def test_oob(self):
        with self.assertRaises(IndexError):
            MorphologySet([self._fake_loader("ello")], [0, 1, 0])

    def test_hard_cache(self):
        cached = self.sets[1].iter_morphologies(hard_cache=True)
        d = None
        self.assertTrue(
            all((d := c if d is None else d) is d for c in cached),
            "hard cache should be ident",
        )
        uncached = self.sets[1].iter_morphologies()
        d = None
        self.assertTrue(
            all((d := c if d is None else d) is d for c in list(cached)[1:]),
            "soft cache should not be ident",
        )

    def test_unique(self):
        self.assertEqual(
            1,
            len([*self.sets[1].iter_morphologies(unique=True)]),
            "only 1 morph in unique set",
        )
        self.assertEqual(
            1, len([*self.sets[1].iter_meta(unique=True)]), "only 1 morph in unique set"
        )


class TestMorphometry(NumpyTestCase, unittest.TestCase):
    def setUp(self):
        # Toy branches
        self.b0 = Branch([], [])
        self.bzero1 = Branch([[0] * 3], [1])
        self.bzero_r1 = Branch([[1] * 3], [0])
        self.b1 = Branch([[1] * 3], [1])
        self.bzero2 = Branch([[0] * 3] * 2, [1] * 2)
        self.bzero_r2 = Branch([[0] * 3] * 2, [0] * 2)
        self.b2 = Branch([[1] * 3, [2] * 3], [1] * 2)
        self.bzero10 = Branch([[0] * 3] * 10, [1] * 10)
        self.bzero_r10 = Branch([[1] * 3] * 10, [0] * 10)
        self.b3 = Branch([[0, 0, 0], [3, 6 * np.sin(np.pi / 3), 0], [6, 0, 0]], [1] * 3)
        # Meaningful toy morphology
        m = Morphology.from_swc(get_morphology_path("test_morphometry.swc"))
        self.adjacency = m.branch_adjacency
        self.branches = m.branches

    def test_empty_branch(self):
        for attr in (
            "euclidean_dist",
            "path_dist",
            "vector",
            "versor",
            "start",
            "max_displacement",
        ):
            with self.subTest(attr=attr):
                with self.assertRaises(EmptyBranchError):
                    getattr(self.b0, attr)

    def test_zero_len(self):
        for attr in ("euclidean_dist", "path_dist"):
            with self.subTest(attr=attr):
                self.assertEqual(getattr(self.b1, attr), 0)
                self.assertEqual(getattr(self.bzero1, attr), 0)
                self.assertEqual(getattr(self.bzero_r1, attr), 0)
                self.assertEqual(getattr(self.bzero2, attr), 0)
                self.assertEqual(getattr(self.bzero_r2, attr), 0)
                self.assertEqual(getattr(self.bzero10, attr), 0)
                self.assertEqual(getattr(self.bzero_r10, attr), 0)

    def test_known_len(self):
        self.assertClose(self.b3.path_dist, 12)
        self.assertClose(self.b3.euclidean_dist, 6)

    def test_adjacency(self):
        known_adj = {0: [1, 2], 1: [], 2: [3, 4, 5], 3: [], 4: [], 5: []}
        self.assertEqual(len(self.branches[0].children), 2)
        self.assertEqual(len(self.branches[2].children), 3)
        self.assertDictEqual(known_adj, self.adjacency)

    def test_start_end(self):
        self.assertClose(self.branches[0].start, [0.0, 1.0, 0.0])
        self.assertClose(self.branches[0].end, [0.0, 1.0, 0.0])
        self.assertClose(self.branches[1].start, [0.0, 1.0, 0.0])
        self.assertClose(self.branches[1].end, [-5.0, np.exp(5), 0.0])
        self.assertClose(self.branches[2].start, [0.0, 1.0, 0.0])
        self.assertClose(self.branches[2].end, [0.0, 11.0, 0.0])
        self.assertClose(self.branches[3].start, [0.0, 11.0, 0.0])
        self.assertClose(
            self.branches[3].end,
            [0.0 + 10 * np.cos(np.pi / 2), 11.0 + 10 * np.sin(np.pi / 2), 0.0],
        )
        self.assertClose(self.branches[4].start, [0.0, 11.0, 0.0])
        self.assertClose(
            self.branches[4].end,
            [0.0 + 10 * np.cos(np.pi / 3), 11.0 + 10 * np.sin(np.pi / 3), 0.0],
        )
        self.assertClose(self.branches[5].start, [0.0, 11.0, 0.0])
        self.assertClose(
            self.branches[5].end,
            [
                0.0 + 10 * np.cos((2 / 3) * np.pi),
                11.0 + 10 * np.sin((2 / 3) * np.pi),
                0.0,
            ],
        )

    def test_vectors(self):
        self.assertClose(self.branches[2].versor, [0.0, 1.0, 0.0])
        self.assertClose(self.branches[2].vector, [0.0, 10.0, 0.0])
        self.assertClose(self.branches[3].versor, [0, 1.0, 0.0])
        self.assertClose(self.branches[3].vector, [0, 10.0, 0.0])
        self.assertClose(
            self.branches[4].versor, [np.cos(np.pi / 3), np.sin(np.pi / 3), 0.0]
        )
        self.assertClose(
            self.branches[5].versor,
            [np.cos((2 / 3) * np.pi), np.sin((2 / 3) * np.pi), 0.0],
        )

        pass

    def test_displacement(self):
        self.assertClose(self.branches[2].max_displacement, 5.0)
        for b in self.branches[3:]:
            self.assertClose(b.max_displacement, 0, atol=1e-06)

    def test_fractal_dim(self):
        for b in self.branches[3:]:
            self.assertClose(b.fractal_dim, 1.0)


class TestSwcFiles(NumpyTestCase, unittest.TestCase):
    # Helper functions to create a toy morphology
    def generate_semicircle(self, center_x, center_y, radius, stepsize=0.01):
        x = np.arange(center_x, center_x + radius + stepsize, stepsize)
        y = np.sqrt(radius**2 - x**2)

        x = np.concatenate([x, x[::-1]])
        y = np.concatenate([y, -y[::-1]])
        z = np.zeros(y.shape)

        return x, y + center_y, z

    def generate_exponential(self, center_x, center_y, len=10, stepsize=0.1):
        x = np.arange(center_x, center_x + len + stepsize, stepsize)
        y = np.exp(x)
        z = np.zeros(y.shape)

        return -x, y + center_y, z

    def generate_radius(
        self, origin_x, origin_y, len=10, angle=(np.pi / 2), stepsize=0.1
    ):
        l = np.arange(0, len + stepsize, stepsize)
        x = l * np.cos(angle) + origin_x
        y = l * np.sin(angle) + origin_y
        z = np.zeros(y.shape)

        return x, y, z

    def setUp(self):
        # Creating the branches
        x_s, y_s, z_s = self.generate_semicircle(0, 6, 5, 0.01)
        x_e, y_e, z_e = self.generate_exponential(0, 0, 5, 0.01)
        x_ri, y_ri, z_ri = self.generate_radius(0, 11, len=10)
        x_rii, y_rii, z_rii = self.generate_radius(0, 11, angle=np.pi / 3, len=10)
        x_riii, y_riii, z_riii = self.generate_radius(
            0, 11, angle=(2 / 3) * np.pi, len=10
        )

        root = Branch(np.array([0.0, 1.0, 0.0]).reshape(1, 3), radii=1)
        exp_child = Branch(np.vstack((x_e, y_e, z_e)).T, radii=[1] * len(x_e))
        semi_child = Branch(np.vstack((x_s, y_s[::-1], z_s)).T, radii=[1] * len(x_s))
        ri_child = Branch(np.vstack((x_ri, y_ri, z_ri)).T, radii=[1] * len(x_ri))
        rii_child = Branch(np.vstack((x_rii, y_rii, z_rii)).T, radii=[1] * len(x_rii))
        riii_child = Branch(
            np.vstack((x_riii, y_riii, z_riii)).T, radii=[1] * len(x_riii)
        )
        semi_child.attach_child(ri_child)
        semi_child.attach_child(rii_child)
        semi_child.attach_child(riii_child)
        root.attach_child(exp_child)
        root.attach_child(semi_child)

        self.m = Morphology([root])

    def test_identity(self):
        m = Morphology.from_swc(get_morphology_path("test_morphometry.swc"))
        self.assertClose(m.points, self.m.points)
