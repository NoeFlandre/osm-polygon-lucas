"""Official LUCAS C3 hierarchy as three TaxonomyLevel maps, keyed on observed codes."""

from collections.abc import Sequence
from typing import Final

from osm_polygon_lucas.polygon_coverage import TaxonomyLevel

# (main, second-level, observed codes), from the official 2022 C3 classification.
# Heading-only codes such as B10 are labels, never keys. A code that is both a heading
# and an observed class, such as C10, keys itself.
_C3_ROWS: Final[tuple[tuple[str, str, tuple[str, ...]], ...]] = (
    ("A00", "A10", ("A11", "A12", "A13")),
    ("A00", "A20", ("A21", "A22")),
    ("A00", "A30", ("A30",)),
    ("B00", "B10", ("B11", "B12", "B13", "B14", "B15", "B16", "B17", "B18", "B19")),
    ("B00", "B20", ("B21", "B22", "B23")),
    ("B00", "B30", ("B31", "B32", "B33", "B34", "B35", "B36", "B37")),
    ("B00", "B40", ("B41", "B42", "B43", "B44", "B45")),
    ("B00", "B50", ("B51", "B52", "B53", "B54", "B55")),
    ("B00", "B70", ("B71", "B72", "B73", "B74", "B75", "B76", "B77")),
    ("B00", "B80", ("B81", "B82", "B83", "B84")),
    ("C00", "C10", ("C10",)),
    ("C00", "C20", ("C21", "C22", "C23")),
    ("C00", "C30", ("C31", "C32", "C33")),
    ("D00", "D10", ("D10",)),
    ("D00", "D20", ("D20",)),
    ("E00", "E10", ("E10",)),
    ("E00", "E20", ("E20",)),
    ("E00", "E30", ("E30",)),
    ("F00", "F10", ("F10",)),
    ("F00", "F20", ("F20",)),
    ("F00", "F30", ("F30",)),
    ("F00", "F40", ("F40",)),
    ("G00", "G10", ("G11", "G12")),
    ("G00", "G20", ("G21", "G22")),
    ("G00", "G30", ("G30",)),
    ("G00", "G40", ("G40",)),
    ("G00", "G50", ("G50",)),
    ("H00", "H10", ("H11", "H12")),
    ("H00", "H20", ("H21", "H22", "H23")),
)


def _derive_levels(
    rows: Sequence[tuple[str, str, tuple[str, ...]]],
) -> tuple[TaxonomyLevel, ...]:
    """Raise ValueError when an observed code appears twice."""
    main: dict[str, str] = {}
    second_level: dict[str, str] = {}
    observed: dict[str, str] = {}
    for main_code, second_code, codes in rows:
        for code in codes:
            if code in observed:
                raise ValueError(f"observed code {code} appears more than once")
            main[code] = main_code
            second_level[code] = second_code
            # The observed_code level labels each class by itself.
            observed[code] = code
    return (
        TaxonomyLevel("main_category", main),
        TaxonomyLevel("second_level", second_level),
        TaxonomyLevel("observed_code", observed),
    )


LUCAS_C3_LEVELS: Final[tuple[TaxonomyLevel, ...]] = _derive_levels(_C3_ROWS)
