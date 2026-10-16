import hashlib
import json
import os
from io import BytesIO
from types import SimpleNamespace

import pytest
from lucas_gpkg_builder import feature, write_lucas_gpkg
from shapely.geometry import Polygon, box

from osm_polygon_lucas import cli
from osm_polygon_lucas.cli import main


def test_cli_emits_deterministic_json_with_source_and_all_point_evidence(
    tmp_path, capsys
):
    gpkg_path = write_lucas_gpkg(
        tmp_path / "synthetic.gpkg",
        [
            feature(12, box(0.0, 0.0, 8.0, 10.0), surveycprnlc="C10"),
            feature(99, box(100.0, 100.0, 101.0, 101.0), surveycprnlc="C10"),
        ],
    )
    wkt_path = tmp_path / "osm.wkt"
    wkt = "POLYGON((0 0,10 0,10 10,0 10,0 0))\n"
    wkt_path.write_text(wkt, encoding="utf-8")
    args = [
        "--osm-wkt",
        str(wkt_path),
        "--osm-crs",
        "EPSG:3035",
        "--lucas-gpkg",
        str(gpkg_path),
    ]

    assert main(args) == 0
    first = capsys.readouterr().out
    payload = json.loads(first)

    assert list(payload) == [
        "input",
        "levels",
        "policy",
        "schema",
        "source",
        "source_selection",
        "supports",
    ]
    assert first.startswith('{\n  "input": {\n')
    assert first.endswith("}\n")
    assert payload["schema"] == "lucas_polygon_coverage.v1"
    assert payload["input"] == {
        "osm_crs": "EPSG:3035",
        "osm_area": 100.0,
        "osm_wkt_sha256": hashlib.sha256(wkt.encode()).hexdigest(),
    }
    assert payload["source"] == {
        "asset": "l2022_survey_cop_radpoly_attr.gpkg",
        "layer": "l2022_cop_radpoly_radqs_allatr_wcol",
        "crs": "EPSG:3035",
        "expected_sha256": (
            "4d240c5710866f9549712685859a5051522b223f3e1fe580dcc9e398b68b037f"
        ),
        "sha256_verification": "not_performed_by_cli",
    }
    assert payload["policy"] == {
        "minimum_class_fraction": 0.8,
        "comparison": ">=",
        "denominator": "full_original_osm_area",
        "same_class_supports": "unioned_before_measurement",
        "taxonomy_levels_aggregated_before_gate": True,
        "cross_class_overlaps": "retained_as_evidence",
        "multiple_gate_classes": "ambiguous",
        "missing_support": "remains_in_denominator",
    }
    assert payload["source_selection"] == {
        "crs": "EPSG:3035",
        "bounds": [0.0, 0.0, 10.0, 10.0],
        "eligible_bbox_candidate_count": 1,
        "rejections": [],
    }
    assert payload["supports"] == [
        {
            "source_feature_id": "12",
            "point_id": "900001",
            "raw_support_class": "C10",
            "survey_date": "2022-09-04",
            "target_coordinates": {"lat": 45.5, "long": 12.5},
            "observer_gps": {"lat": 45.25, "long": 12.75},
            "core_labels": {
                "survey_lc1": "B11",
                "survey_lc2": None,
                "survey_lu1": "U111",
                "survey_lu2": None,
            },
            "intersection_area": 80.0,
            "boundary_only": False,
        }
    ]
    main(args)
    second = capsys.readouterr().out
    assert second == first
    main_category, *_ = payload["levels"]
    assert main_category["name"] == "main_category"
    assert main_category["status"] == "winner"
    assert main_category["winner"] == "C00"
    assert main_category["classes"] == [
        {
            "label": "C00",
            "source_feature_ids": ["12"],
            "covered_area": 80.0,
            "fraction": 0.8,
            "meets_gate": True,
        }
    ]


def test_cli_help_preserves_the_declared_command_and_option_descriptions():
    help_text = cli._argument_parser().format_help()
    normalized_help = f" {' '.join(help_text.split())} "

    assert help_text.startswith("usage: lucas-cover ")
    for phrase in (
        "Measure LUCAS 2022 class coverage for one EPSG:3035 OSM polygon.",
        "UTF-8 WKT file containing one polygon, or '-' to read stdin.",
        "CRS of the supplied WKT. The LUCAS source uses EPSG:3035.",
        "Path to the pinned LUCAS 2022 Copernicus GeoPackage.",
        "JSON output path, or '-' for stdout (default).",
    ):
        assert f" {phrase} " in normalized_help


def test_source_manifest_uses_exact_package_and_resource_name(monkeypatch):
    source = '{"asset":"café"}'.encode()

    class Resource:
        def read_text(self, encoding=None):
            return source.decode(encoding or "ascii")

    class Package:
        def joinpath(self, name):
            assert name == "lucas_2022_source.json"
            return Resource()

    def fake_files(package):
        assert package == "osm_polygon_lucas"
        return Package()

    monkeypatch.setattr(cli, "files", fake_files)

    assert cli._source_manifest() == {"asset": "café"}


def test_empty_wkt_error_is_specific(tmp_path):
    wkt_path = tmp_path / "empty.wkt"
    wkt_path.write_bytes(b" \n")

    with pytest.raises(ValueError) as error:
        cli._read_osm_wkt(str(wkt_path))

    assert str(error.value) == "OSM WKT input is empty"


def test_output_file_is_utf8(tmp_path):
    output_path = tmp_path / "coverage.json"
    output = '{"label":"漢字"}\n'

    cli._write_output(str(output_path), output)

    assert output_path.read_bytes() == output.encode("utf-8")


def test_temporary_output_opens_with_explicit_utf8_encoding(tmp_path, monkeypatch):
    output_path = tmp_path / "coverage.json"
    original_open = cli.Path.open
    encodings = []

    def recording_open(path, *args, **kwargs):
        encodings.append(kwargs.get("encoding"))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(cli.Path, "open", recording_open)

    cli._write_output(str(output_path), "report")

    assert encodings == ["utf-8"]


def test_output_file_uses_normal_creation_permissions(tmp_path):
    output_path = tmp_path / "coverage.json"
    original_umask = os.umask(0o027)
    try:
        cli._write_output(str(output_path), "report")
    finally:
        os.umask(original_umask)

    assert output_path.stat().st_mode & 0o777 == 0o640


def test_output_replacement_preserves_existing_permissions(tmp_path):
    output_path = tmp_path / "coverage.json"
    output_path.write_text("old report", encoding="utf-8")
    output_path.chmod(0o640)

    cli._write_output(str(output_path), "new report")

    assert output_path.read_text(encoding="utf-8") == "new report"
    assert output_path.stat().st_mode & 0o777 == 0o640


def test_output_replacement_preserves_executable_permissions(tmp_path):
    output_path = tmp_path / "coverage.json"
    output_path.write_text("old report", encoding="utf-8")
    output_path.chmod(0o751)

    cli._write_output(str(output_path), "new report")

    assert output_path.read_text(encoding="utf-8") == "new report"
    assert output_path.stat().st_mode & 0o777 == 0o751


def test_output_skips_existing_temporary_directory_without_touching_it(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cli, "_temporary_directory_parent", lambda path: path.parent)
    collision_directory = tmp_path / ".lucas-cover-0"
    collision_directory.mkdir()
    marker_path = collision_directory / "keep"
    marker_path.write_text("pre-existing", encoding="utf-8")
    output_path = tmp_path / "coverage.json"

    cli._write_output(str(output_path), "new report")

    assert output_path.read_text(encoding="utf-8") == "new report"
    assert marker_path.read_text(encoding="utf-8") == "pre-existing"
    assert not (tmp_path / ".lucas-cover-1").exists()


def test_output_symlink_keeps_target_behavior(tmp_path):
    target_path = tmp_path / "target.json"
    target_path.write_text("old report", encoding="utf-8")
    output_path = tmp_path / "coverage.json"
    output_path.symlink_to(target_path)

    cli._write_output(str(output_path), "new report")

    assert output_path.is_symlink()
    assert target_path.read_text(encoding="utf-8") == "new report"


@pytest.mark.parametrize("input_option", ["--osm-wkt", "--lucas-gpkg"])
def test_cli_rejects_output_that_would_overwrite_an_input(
    input_option, tmp_path, capsys
):
    gpkg_path = write_lucas_gpkg(
        tmp_path / "synthetic.gpkg", [feature(12, box(0.0, 0.0, 10.0, 10.0))]
    )
    wkt_path = tmp_path / "osm.wkt"
    wkt_path.write_text("POLYGON((0 0,10 0,10 10,0 10,0 0))", encoding="utf-8")
    input_path = wkt_path if input_option == "--osm-wkt" else gpkg_path
    original = input_path.read_bytes()

    with pytest.raises(SystemExit) as error:
        main(
            [
                "--osm-wkt",
                str(wkt_path),
                "--osm-crs",
                "EPSG:3035",
                "--lucas-gpkg",
                str(gpkg_path),
                "--output",
                str(input_path),
            ]
        )

    assert error.value.code == 2
    assert "would overwrite input file" in capsys.readouterr().err
    assert input_path.read_bytes() == original


def test_output_rejects_hard_link_to_protected_input(tmp_path):
    input_path = tmp_path / "input.wkt"
    input_path.write_bytes(b"POLYGON((0 0,1 0,1 1,0 1,0 0))")
    output_path = tmp_path / "output.wkt"
    output_path.hardlink_to(input_path)
    original = input_path.read_bytes()

    with pytest.raises(ValueError, match="would overwrite input file"):
        cli._write_output(
            str(output_path), "replacement", protected_paths=(str(input_path),)
        )

    assert input_path.read_bytes() == original
    assert output_path.read_bytes() == original


@pytest.mark.parametrize("alias_kind", ["symlink", "hard_link"])
def test_output_does_not_follow_alias_added_after_input_check(
    alias_kind, tmp_path, monkeypatch
):
    gpkg_path = write_lucas_gpkg(
        tmp_path / "synthetic.gpkg", [feature(12, box(0.0, 0.0, 10.0, 10.0))]
    )
    input_path = tmp_path / "osm.wkt"
    input_path.write_text("POLYGON((0 0,10 0,10 10,0 10,0 0))", encoding="utf-8")
    output_path = tmp_path / "coverage.json"
    output_path.write_text("old report", encoding="utf-8")
    original = input_path.read_bytes()
    samefile = cli.Path.samefile

    def add_alias_after_check(first, second):
        result = samefile(first, second)
        if first == output_path and second == input_path:
            output_path.unlink()
            if alias_kind == "symlink":
                output_path.symlink_to(input_path)
            else:
                output_path.hardlink_to(input_path)
        return result

    monkeypatch.setattr(cli.Path, "samefile", add_alias_after_check)

    assert (
        main(
            [
                "--osm-wkt",
                str(input_path),
                "--osm-crs",
                "EPSG:3035",
                "--lucas-gpkg",
                str(gpkg_path),
                "--output",
                str(output_path),
            ]
        )
        == 0
    )

    assert input_path.read_bytes() == original
    assert json.loads(output_path.read_text(encoding="utf-8"))["schema"] == (
        "lucas_polygon_coverage.v1"
    )


def test_output_allows_a_distinct_existing_target(tmp_path):
    input_path = tmp_path / "input.wkt"
    input_path.write_text("source", encoding="utf-8")
    output_path = tmp_path / "coverage.json"
    output_path.write_text("old report", encoding="utf-8")

    cli._write_output(
        str(output_path), "new report", protected_paths=(str(input_path),)
    )

    assert output_path.read_text(encoding="utf-8") == "new report"


def test_output_file_checks_sources_after_standard_input_marker(tmp_path):
    input_path = tmp_path / "synthetic.gpkg"
    input_path.write_bytes(b"GeoPackage")
    original = input_path.read_bytes()

    with pytest.raises(ValueError, match="would overwrite input file"):
        cli._write_output(
            str(input_path),
            "report",
            protected_paths=(None, str(input_path)),
        )

    assert input_path.read_bytes() == original


def test_output_path_named_dash_is_allowed_when_stdin_is_an_input(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    output_path = tmp_path / "-"

    cli._write_output("./-", "report", protected_paths=(None,))

    assert output_path.read_text(encoding="utf-8") == "report"


def test_cli_protects_a_geopackage_named_dash_when_wkt_comes_from_stdin(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    gpkg_path = write_lucas_gpkg(
        tmp_path / "-", [feature(12, box(0.0, 0.0, 10.0, 10.0))]
    )
    original = gpkg_path.read_bytes()
    wkt = b"POLYGON((0 0,10 0,10 10,0 10,0 0))"
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(buffer=BytesIO(wkt)))

    with pytest.raises(SystemExit) as error:
        main(
            [
                "--osm-wkt",
                "-",
                "--osm-crs",
                "EPSG:3035",
                "--lucas-gpkg",
                "-",
                "--output",
                "./-",
            ]
        )

    assert error.value.code == 2
    assert "would overwrite input file" in capsys.readouterr().err
    assert gpkg_path.read_bytes() == original


def test_cli_allows_output_named_dash_when_wkt_comes_from_stdin(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gpkg_path = write_lucas_gpkg(
        tmp_path / "synthetic.gpkg", [feature(12, box(0.0, 0.0, 10.0, 10.0))]
    )
    original = gpkg_path.read_bytes()
    wkt = b"POLYGON((0 0,10 0,10 10,0 10,0 0))"
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(buffer=BytesIO(wkt)))

    assert (
        main(
            [
                "--osm-wkt",
                "-",
                "--osm-crs",
                "EPSG:3035",
                "--lucas-gpkg",
                str(gpkg_path),
                "--output",
                "./-",
            ]
        )
        == 0
    )

    assert json.loads((tmp_path / "-").read_text(encoding="utf-8"))["schema"] == (
        "lucas_polygon_coverage.v1"
    )
    assert gpkg_path.read_bytes() == original


def test_cli_reports_an_output_symlink_loop_without_a_traceback(tmp_path, capsys):
    gpkg_path = write_lucas_gpkg(
        tmp_path / "synthetic.gpkg", [feature(12, box(0.0, 0.0, 10.0, 10.0))]
    )
    wkt_path = tmp_path / "osm.wkt"
    wkt_path.write_text("POLYGON((0 0,10 0,10 10,0 10,0 0))", encoding="utf-8")
    output_path = tmp_path / "loop"
    output_path.symlink_to(output_path.name)

    with pytest.raises(SystemExit) as error:
        main(
            [
                "--osm-wkt",
                str(wkt_path),
                "--osm-crs",
                "EPSG:3035",
                "--lucas-gpkg",
                str(gpkg_path),
                "--output",
                str(output_path),
            ]
        )

    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert "error:" in stderr
    assert "output path contains a symlink loop" in stderr
    assert "Traceback" not in stderr


def test_input_alias_check_reports_a_symlink_loop(tmp_path):
    output_path = tmp_path / "coverage.json"
    loop_path = tmp_path / "loop"
    loop_path.symlink_to(loop_path.name)

    with pytest.raises(
        ValueError, match="^output or input path contains a symlink loop$"
    ):
        cli._paths_identify_same_file(output_path, loop_path)


def test_cli_requires_wkt_crs_and_geopackage_arguments(tmp_path):
    gpkg_path = write_lucas_gpkg(
        tmp_path / "synthetic.gpkg", [feature(12, box(0.0, 0.0, 10.0, 10.0))]
    )
    wkt_path = tmp_path / "osm.wkt"
    wkt_path.write_text("POLYGON((0 0,10 0,10 10,0 10,0 0))", encoding="utf-8")
    complete = [
        "--osm-wkt",
        str(wkt_path),
        "--osm-crs",
        "EPSG:3035",
        "--lucas-gpkg",
        str(gpkg_path),
    ]

    for option in ("--osm-wkt", "--osm-crs", "--lucas-gpkg"):
        args = list(complete)
        index = args.index(option)
        del args[index : index + 2]
        with pytest.raises(SystemExit) as error:
            main(args)
        assert error.value.code == 2


def test_cli_rejects_an_undeclared_or_unsupported_osm_crs(tmp_path):
    gpkg_path = write_lucas_gpkg(tmp_path / "synthetic.gpkg", [])
    wkt_path = tmp_path / "osm.wkt"
    wkt_path.write_text("POLYGON((0 0,10 0,10 10,0 10,0 0))", encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        main(
            [
                "--osm-wkt",
                str(wkt_path),
                "--osm-crs",
                "EPSG:4326",
                "--lucas-gpkg",
                str(gpkg_path),
            ]
        )

    assert error.value.code == 2


def test_cli_keeps_boundary_overlap_and_rejection_evidence(tmp_path, capsys):
    gpkg_path = write_lucas_gpkg(
        tmp_path / "synthetic.gpkg",
        [
            feature(12, box(0.0, 0.0, 9.0, 10.0), surveycprnlc="C10"),
            feature(13, box(1.0, 0.0, 10.0, 10.0), surveycprnlc="B11"),
            feature(14, box(10.0, 0.0, 12.0, 10.0), surveycprnlc="E10"),
            feature(
                16,
                Polygon(
                    shell=[(-1, -1), (11, -1), (11, 11), (-1, 11), (-1, -1)],
                    holes=[
                        [
                            (-0.5, -0.5),
                            (10.5, -0.5),
                            (10.5, 10.5),
                            (-0.5, 10.5),
                            (-0.5, -0.5),
                        ]
                    ],
                ),
                surveycprnlc="D10",
            ),
            feature(
                15,
                box(0.0, 0.0, 10.0, 10.0),
                survey_obs_type="Field Survey, point visible > 100 m",
            ),
        ],
    )
    wkt_path = tmp_path / "osm.wkt"
    wkt_path.write_text("POLYGON((0 0,10 0,10 10,0 10,0 0))", encoding="utf-8")

    assert (
        main(
            [
                "--osm-wkt",
                str(wkt_path),
                "--osm-crs",
                "EPSG:3035",
                "--lucas-gpkg",
                str(gpkg_path),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["source_selection"]["eligible_bbox_candidate_count"] == 4
    assert payload["source_selection"]["rejections"] == [
        {"source_feature_id": "15", "reasons": ["survey_obs_type"]}
    ]
    assert [
        (item["source_feature_id"], item["intersection_area"], item["boundary_only"])
        for item in payload["supports"]
    ] == [("12", 90.0, False), ("13", 90.0, False), ("14", 0.0, True)]
    main_category = payload["levels"][0]
    assert main_category["status"] == "ambiguous"
    assert main_category["winner"] is None
    assert main_category["overlaps"] == [
        {
            "first_feature_id": "12",
            "second_feature_id": "13",
            "area": 80.0,
            "relation": "cross_class",
        }
    ]


def test_cli_reads_wkt_from_stdin(tmp_path, monkeypatch, capsys):
    gpkg_path = write_lucas_gpkg(
        tmp_path / "synthetic.gpkg", [feature(12, box(0.0, 0.0, 10.0, 10.0))]
    )
    wkt = b"POLYGON((0 0,10 0,10 10,0 10,0 0))"
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(buffer=BytesIO(wkt)))

    assert (
        main(
            [
                "--osm-wkt",
                "-",
                "--osm-crs",
                "EPSG:3035",
                "--lucas-gpkg",
                str(gpkg_path),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["input"]["osm_wkt_sha256"] == hashlib.sha256(wkt).hexdigest()
    assert payload["levels"][0]["winner"] == "C00"


def test_cli_preserves_null_dates_and_unmapped_support_ids(tmp_path, capsys):
    gpkg_path = write_lucas_gpkg(
        tmp_path / "synthetic.gpkg",
        [
            feature(
                21,
                box(0.0, 0.0, 10.0, 10.0),
                surveycprnlc="Z99",
                survey_date=None,
            )
        ],
    )
    wkt_path = tmp_path / "osm.wkt"
    wkt_path.write_text("POLYGON((0 0,10 0,10 10,0 10,0 0))", encoding="utf-8")

    assert (
        main(
            [
                "--osm-wkt",
                str(wkt_path),
                "--osm-crs",
                "EPSG:3035",
                "--lucas-gpkg",
                str(gpkg_path),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["supports"][0]["survey_date"] is None
    assert payload["levels"][0]["unmapped_feature_ids"] == ["21"]


def test_cli_writes_report_to_requested_path(tmp_path, capsys):
    gpkg_path = write_lucas_gpkg(
        tmp_path / "synthetic.gpkg", [feature(12, box(0.0, 0.0, 10.0, 10.0))]
    )
    wkt_path = tmp_path / "osm.wkt"
    wkt_path.write_text("POLYGON((0 0,10 0,10 10,0 10,0 0))", encoding="utf-8")
    output_path = tmp_path / "coverage.json"

    assert (
        main(
            [
                "--osm-wkt",
                str(wkt_path),
                "--osm-crs",
                "EPSG:3035",
                "--lucas-gpkg",
                str(gpkg_path),
                "--output",
                str(output_path),
            ]
        )
        == 0
    )

    assert capsys.readouterr().out == ""
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema"] == "lucas_polygon_coverage.v1"


def test_cli_reports_output_write_errors_as_usage_errors(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_report_text", lambda args: "{}\n")
    output_path = tmp_path / "missing" / "coverage.json"

    with pytest.raises(SystemExit) as error:
        main(
            [
                "--osm-wkt",
                "unused.wkt",
                "--osm-crs",
                "EPSG:3035",
                "--lucas-gpkg",
                "unused.gpkg",
                "--output",
                str(output_path),
            ]
        )

    assert error.value.code == 2
    assert "No such file or directory" in capsys.readouterr().err


def test_cli_rejects_non_finite_evidence_instead_of_emitting_invalid_json(
    tmp_path, capsys
):
    gpkg_path = write_lucas_gpkg(
        tmp_path / "synthetic.gpkg",
        [feature(12, box(0.0, 0.0, 10.0, 10.0), point_lat=float("inf"))],
    )
    wkt_path = tmp_path / "osm.wkt"
    wkt_path.write_text("POLYGON((0 0,10 0,10 10,0 10,0 0))", encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        main(
            [
                "--osm-wkt",
                str(wkt_path),
                "--osm-crs",
                "EPSG:3035",
                "--lucas-gpkg",
                str(gpkg_path),
            ]
        )

    assert error.value.code == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    ("wkt", "message"),
    [
        ("", "empty"),
        ("NOT WKT", "invalid OSM WKT"),
        ("POINT(0 0)", "Polygon or MultiPolygon"),
        ("POLYGON EMPTY", "positive area"),
        ("POLYGON((0 0,10 10,0 10,10 0,0 0))", "must be valid"),
        (
            "POLYGON((0 0,1e200 0,1e200 1e200,0 1e200,0 0))",
            "area must be finite",
        ),
    ],
    ids=[
        "empty-input",
        "malformed",
        "non-polygon",
        "empty-polygon",
        "invalid",
        "infinite-area",
    ],
)
def test_cli_rejects_invalid_wkt_before_opening_source(tmp_path, capsys, wkt, message):
    wkt_path = tmp_path / "osm.wkt"
    wkt_path.write_text(wkt, encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        main(
            [
                "--osm-wkt",
                str(wkt_path),
                "--osm-crs",
                "EPSG:3035",
                "--lucas-gpkg",
                str(tmp_path / "not-opened.gpkg"),
            ]
        )

    assert error.value.code == 2
    assert message in capsys.readouterr().err
