import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import hflib  # noqa: E402
import ingest  # noqa: E402

SYMBOL = (
    '(symbol "X1"\n'
    '\t\t(property "Footprint" "FP"\n\t\t\t(at 0 0 0)\n\t\t)\n'
    '\t\t(property "Description" ""\n\t\t\t(at 0 0 0)\n\t\t)\n'
    '\t\t(property "Description_1" "a (tricky) \\"value\\""\n\t\t\t(at 0 0 0)\n\t\t)\n'
    '\t\t(symbol "X1_0_1"\n\t\t\t(pin passive line\n\t\t\t\t(at 0 0 0)\n'
    '\t\t\t\t(number "1"\n\t\t\t\t)\n\t\t\t)\n\t\t)\n'
    "\t)"
)

FOOTPRINT = (
    '(footprint "FP"\n\t(version 20260206)\n'
    '\t(pad "1" smd rect\n\t\t(at 0 0)\n\t\t(size 1 1)\n\t\t(uuid "aaaaaaaa-0000-0000-0000-000000000001")\n\t)\n'
    '\t(model "Part.stp"\n\t\t(offset\n\t\t\t(xyz 0 0 0)\n\t\t)\n\t)\n)\n'
)


class SexprTests(unittest.TestCase):
    def test_children_ignores_parens_in_strings(self):
        names = [hflib.get_prop(SYMBOL, "Description_1")]
        self.assertEqual(names, ['a (tricky) "value"'])
        self.assertEqual(len(hflib.children(SYMBOL)), 4)

    def test_set_and_remove_prop(self):
        t = hflib.set_prop(SYMBOL, "Footprint", "HF_Footprints:FP")
        self.assertEqual(hflib.get_prop(t, "Footprint"), "HF_Footprints:FP")
        t = hflib.remove_prop(t, "Description_1")
        self.assertIsNone(hflib.get_prop(t, "Description_1"))
        self.assertEqual(hflib.get_prop(t, "Description"), "")

    def test_add_prop_when_missing(self):
        t = hflib.set_prop(SYMBOL, "Manufacturer_Name", 'A "B"')
        self.assertEqual(hflib.get_prop(t, "Manufacturer_Name"), 'A "B"')
        self.assertEqual(hflib.pin_numbers(t), {"1"})

    def test_pin_numbers(self):
        self.assertEqual(hflib.pin_numbers(SYMBOL), {"1"})


class LibraryTests(unittest.TestCase):
    def test_symbol_lib_roundtrip_sorted(self):
        cfg = hflib.load_config()
        with tempfile.TemporaryDirectory() as d:
            lib = hflib.SymbolLib(Path(d) / "L.kicad_sym", cfg)
            lib.symbols["b"] = SYMBOL.replace("X1", "b")
            lib.symbols["A"] = SYMBOL.replace("X1", "A")
            lib.save()
            again = hflib.SymbolLib(Path(d) / "L.kicad_sym", cfg)
            self.assertEqual(list(again.symbols), ["A", "b"])
            self.assertEqual(set(again.symbols), {"A", "b"})
            self.assertEqual(again.render(), lib.render())

    def test_pad_signature_ignores_uuid_and_name(self):
        other = FOOTPRINT.replace("aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002")
        self.assertEqual(hflib.pad_signature(FOOTPRINT), hflib.pad_signature(other))
        wider = FOOTPRINT.replace("(size 1 1)", "(size 1 2)")
        self.assertNotEqual(hflib.pad_signature(FOOTPRINT), hflib.pad_signature(wider))

    def test_model_rewrite(self):
        t = hflib.set_model(FOOTPRINT, "${V}/m/FP.step")
        self.assertEqual(hflib.model_path(t), "${V}/m/FP.step")
        self.assertIsNone(hflib.model_path(hflib.drop_model(t)))
        added = hflib.set_model(hflib.drop_model(FOOTPRINT), "${V}/m/FP.step")
        self.assertEqual(hflib.model_path(added), "${V}/m/FP.step")
        self.assertEqual(hflib.pad_names(added), {"1"})

    def test_rename_footprint(self):
        self.assertEqual(hflib.fp_name(hflib.rename_fp(FOOTPRINT, "FP_X")), "FP_X")


class SnapEdaTests(unittest.TestCase):
    TEXT = (
        '(symbol "P1"\n'
        '\t\t(property "MF" "ACME"\n\t\t\t(at 0 0 0)\n\t\t)\n'
        '\t\t(property "MP" "P1"\n\t\t\t(at 0 0 0)\n\t\t)\n'
        '\t\t(property "Package" "None"\n\t\t\t(at 0 0 0)\n\t\t)\n'
        '\t\t(property "Purchase-URL" "https://x/?unipart_id=42&a=b"\n\t\t\t(at 0 0 0)\n\t\t)\n'
        '\t\t(property "SnapEDA_Link" "https://snapeda.com/p"\n\t\t\t(at 0 0 0)\n\t\t)\n'
        "\t)"
    )

    def test_field_mapping(self):
        text, meta = ingest.adapt_snapeda(self.TEXT)
        self.assertEqual(hflib.get_prop(text, "Manufacturer_Name"), "ACME")
        self.assertEqual(hflib.get_prop(text, "Manufacturer_Part_Number"), "P1")
        for gone in ("MF", "MP", "Package"):
            self.assertIsNone(hflib.get_prop(text, gone))
        self.assertEqual(meta["PartID"], "42")
        self.assertEqual(meta["SourceURL"], "https://snapeda.com/p")

    def test_newline_escaping_roundtrip(self):
        t = hflib.set_prop(self.TEXT, "Description", "a\nb")
        self.assertIn('"a\\nb"', t)
        self.assertEqual(hflib.get_prop(t, "Description"), "a\nb")


if __name__ == "__main__":
    unittest.main()
