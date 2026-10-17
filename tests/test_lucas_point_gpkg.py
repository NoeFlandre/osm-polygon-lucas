from pathlib import Path

import pytest
from lucas_gpkg_builder import feature, write_lucas_gpkg
from shapely.geometry import box

from osm_polygon_lucas import (
    SourcePolygon,
    evaluate_point_census,
    iter_lucas_2022_point_observations,
)


def test_point_reader_uses_actual_survey_gps_and_copernicus_class(tmp_path: Path):
    path = write_lucas_gpkg(
        tmp_path / "point.gpkg",
        [
            feature(
                7,
                box(0, 0, 1, 1),
                point_id="lucas-7",
                point_lat=50.0,
                point_long=50.0,
                survey_gps_lat="0.5",
                survey_gps_long="0.5",
                surveycprnlc="C10",
                survey_lc1="B11",
            )
        ],
    )

    [observation] = iter_lucas_2022_point_observations(path)
    census = evaluate_point_census(
        [SourcePolygon("osm:way/7", box(0, 0, 1, 1))], [observation]
    )

    assert observation.source_feature_id == "7"
    assert observation.point_id == "lucas-7"
    assert observation.survey_gps_long == "0.5"
    assert observation.surveycprnlc == "C10"
    assert observation.survey_obs_type == 1
    assert census.eligible_polygon_labels == {"osm:way/7": "C00"}


def test_point_reader_keeps_unknown_observation_mode_for_counted_exclusion(tmp_path):
    path = write_lucas_gpkg(
        tmp_path / "point.gpkg",
        [feature(1, box(0, 0, 1, 1), survey_obs_type="new mode")],
    )

    [observation] = iter_lucas_2022_point_observations(path)
    census = evaluate_point_census(
        [SourcePolygon("polygon", box(0, 0, 1, 1))], [observation]
    )

    assert observation.survey_obs_type == "new mode"
    assert census.rejection_counts == {"unknown_survey_obs_type": 1}


def test_point_reader_requires_its_fields_and_does_not_change_source(tmp_path):
    path = write_lucas_gpkg(
        tmp_path / "point.gpkg",
        [feature(1, box(0, 0, 1, 1))],
        omit_columns=["survey_gps_lat"],
    )

    with pytest.raises(ValueError, match="lacks point-census columns.*survey_gps_lat"):
        list(iter_lucas_2022_point_observations(path))


def test_missing_point_geopackage_is_not_created(tmp_path):
    path = tmp_path / "missing.gpkg"

    with pytest.raises(FileNotFoundError):
        next(iter_lucas_2022_point_observations(path))
    assert not path.exists()


def test_point_reader_wraps_unreadable_database_with_path_and_cause(tmp_path):
    path = tmp_path / "not-a-database.gpkg"
    path.write_text("not a sqlite database", encoding="utf-8")

    with pytest.raises(ValueError, match="not a readable LUCAS GeoPackage") as exc_info:
        list(iter_lucas_2022_point_observations(path))

    assert str(path) in str(exc_info.value)
    assert exc_info.value.__cause__ is not None
