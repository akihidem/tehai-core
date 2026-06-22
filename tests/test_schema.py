import unittest

from tehai.schema import validate


class TestMiniSchema(unittest.TestCase):
    def test_required_missing(self):
        s = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
        self.assertTrue(validate({}, s))
        self.assertEqual(validate({"a": "x"}, s), [])

    def test_enum(self):
        s = {"enum": ["small", "medium", "large"]}
        self.assertEqual(validate("small", s), [])
        self.assertTrue(validate("huge", s))

    def test_range(self):
        s = {"type": "integer", "minimum": 0, "maximum": 100}
        self.assertEqual(validate(50, s), [])
        self.assertTrue(validate(101, s))
        self.assertTrue(validate(-1, s))

    def test_bool_is_not_integer(self):
        s = {"type": "integer"}
        self.assertTrue(validate(True, s))

    def test_type_union_null(self):
        s = {"type": ["string", "null"]}
        self.assertEqual(validate(None, s), [])
        self.assertEqual(validate("x", s), [])
        self.assertTrue(validate(3, s))

    def test_additional_properties_false(self):
        s = {"type": "object", "additionalProperties": False, "properties": {"a": {"type": "string"}}}
        self.assertTrue(validate({"a": "x", "b": 1}, s))

    def test_min_items_and_items(self):
        s = {"type": "array", "minItems": 1, "items": {"type": "string"}}
        self.assertTrue(validate([], s))
        self.assertTrue(validate([1], s))
        self.assertEqual(validate(["x"], s), [])


if __name__ == "__main__":
    unittest.main()
