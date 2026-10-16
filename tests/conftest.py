from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest
from shapely.geometry.base import BaseGeometry

from osm_polygon_lucas import EligibleSupport, classify_support_row

MakeSupport = Callable[..., EligibleSupport]


@pytest.fixture
def repository_root(pytestconfig) -> Path:
    for candidate in (pytestconfig.rootpath, *pytestconfig.rootpath.parents):
        root = Path(candidate)
        if (root / "config" / "lucas-2022-source.json").is_file():
            return root
    pytest.fail("could not find the repository source manifest")


@pytest.fixture
def base_row() -> dict[str, object]:
    return {
        "survey_year": 2022,
        "point_copernicus": 1,
        "surveycprncando": 1,
        "survey_obs_type": 1,
        "surveycprnlc": "C10",
        "survey_date": date(2022, 6, 15),
        "point_id": 900001,
        "point_lat": 45.5,
        "point_long": 12.5,
        "survey_gps_lat": 45.25,
        "survey_gps_long": 12.75,
        "survey_lc1": "B11",
        "survey_lc2": None,
        "survey_lu1": "U111",
        "survey_lu2": None,
    }


@pytest.fixture
def make_support(base_row: dict[str, object]) -> MakeSupport:
    def make(
        feature_id: str,
        raw_class: str,
        geometry: BaseGeometry,
        **overrides: object,
    ) -> EligibleSupport:
        row = {**base_row, "surveycprnlc": raw_class, **overrides}
        result = classify_support_row(feature_id, row, geometry)
        if not isinstance(result, EligibleSupport):
            pytest.fail(f"{feature_id} was rejected: {result.reasons}")
        return result

    return make
