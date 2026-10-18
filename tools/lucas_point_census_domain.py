"""Pure transformations for the pinned-source point census."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from osm_polygon_lucas.point_coverage import PointCensus, PointPolygonStatus
from tools.lucas_point_sampling import (
    PointSentenceSample,
    sample_point_sentence_instances,
)

_LABEL_STATUSES = {"yes", "no", "skipped", "failed"}
_BINARY_STATUSES = {"yes", "no"}
_GEOMETRY_EXCLUSIONS = (
    "invalid_wkb",
    "invalid_geojson",
    "non_polygon_geometry",
    "outside_crs84_bounds",
    "invalid_geometry",
    "zero_area",
)


@dataclass(frozen=True, slots=True)
class PointIndex:
    anchors: Mapping[str, tuple[float, float]]

    @property
    def eligible_polygon_anchors(self) -> Mapping[str, tuple[float, float]]:
        return self.anchors


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def geometry_exclusion(geometry: BaseGeometry) -> str | None:
    reason = _geometry_shape_exclusion(geometry)
    if reason is not None:
        return reason
    if _outside_crs84(geometry):
        return "outside_crs84_bounds"
    return None


def _geometry_shape_exclusion(geometry: BaseGeometry) -> str | None:
    if not isinstance(geometry, Polygon | MultiPolygon):
        return "non_polygon_geometry"
    if geometry.is_empty or geometry.area <= 0:
        return "zero_area"
    if not geometry.is_valid:
        return "invalid_geometry"
    return None


def _outside_crs84(geometry: BaseGeometry) -> bool:
    bounds = geometry.bounds
    if not all(map(math.isfinite, bounds)):
        return True
    minx, miny, maxx, maxy = bounds
    return minx < -180 or miny < -90 or maxx > 180 or maxy > 90


def geometry_digest(geometry: BaseGeometry) -> str:
    import shapely

    return hashlib.sha256(shapely.to_wkb(shapely.normalize(geometry))).hexdigest()


def invalid_geometry_digest(value: Any, reason: str) -> str:
    if isinstance(value, bytes):
        encoded = value
    else:
        encoded = (
            value.encode("utf-8")
            if isinstance(value, str)
            else repr(value).encode("utf-8")
        )
    return hashlib.sha256(reason.encode("ascii") + b"\0" + encoded).hexdigest()


def point_decision_digest(point_census: PointCensus) -> str:
    rows = [_decision_record(decision) for decision in point_census.decisions]
    return payload_sha256(rows)


def _decision_record(decision: Any) -> dict[str, Any]:
    return {
        "polygon_id": decision.polygon_id,
        "status": decision.status.value,
        "label": decision.label,
        "located_observations": decision.located_observations,
        "valid_observations": decision.valid_observations,
        "missing_label_observations": decision.missing_label_observations,
        "invalid_label_observations": decision.invalid_label_observations,
        "unmapped_label_observations": decision.unmapped_label_observations,
        "observed_labels": decision.observed_labels,
        "anchor": [decision.anchor_longitude, decision.anchor_latitude],
    }


def point_result(
    point_census: PointCensus,
    polygon_stats: dict[str, Any],
) -> dict[str, Any]:
    status_counts = Counter(
        decision.status.value for decision in point_census.decisions
    )
    unobserved = max(
        0,
        polygon_stats["valid_unique_source_polygons"] - len(point_census.decisions),
    )
    return {
        **polygon_stats,
        "taxonomy_level": point_census.taxonomy_level,
        "taxonomy_mapping_sha256": point_census.taxonomy_mapping_sha256,
        "observation_count": point_census.observation_count,
        "duplicate_records_skipped": point_census.duplicate_records_skipped,
        "quality_valid_observations": point_census.quality_valid_observations,
        "located_observations": point_census.located_observations,
        "valid_labeled_observations": point_census.valid_labeled_observations,
        "boundary_observations": point_census.boundary_observations,
        "outside_observations": point_census.outside_observations,
        "multiple_match_observations": point_census.multiple_match_observations,
        "rejection_counts": dict(sorted(point_census.rejection_counts.items())),
        "input_observation_modes": dict(
            sorted(point_census.input_observation_modes.items())
        ),
        "quality_valid_observation_modes": dict(
            sorted(point_census.quality_valid_observation_modes.items())
        ),
        "located_observation_modes": dict(
            sorted(point_census.located_observation_modes.items())
        ),
        "eligible_polygon_count": point_census.eligible_polygon_count,
        "conflict_polygon_count": point_census.conflict_polygon_count,
        "no_valid_label_polygon_count": point_census.no_valid_label_polygon_count,
        "unobserved_valid_polygon_count": unobserved,
        "point_polygon_status_counts": _point_status_counts(status_counts, unobserved),
        "eligible_polygon_labels": dict(
            sorted(point_census.eligible_polygon_labels.items())
        ),
        "eligible_polygon_anchors": _anchor_records(point_census),
        "point_decision_sha256": point_decision_digest(point_census),
    }


def _point_status_counts(
    status_counts: Counter[str], unobserved: int
) -> dict[str, int]:
    return {
        "eligible": status_counts[PointPolygonStatus.ELIGIBLE.value],
        "conflicting_labels": status_counts[
            PointPolygonStatus.CONFLICTING_LABELS.value
        ],
        "no_valid_label": status_counts[PointPolygonStatus.NO_VALID_LABEL.value],
        "unobserved": unobserved,
    }


def _anchor_records(point_census: PointCensus) -> dict[str, list[float]]:
    anchors = point_census.eligible_polygon_anchors
    return {key: list(anchors[key]) for key in sorted(anchors)}


def point_index_from_result(result: dict[str, Any]) -> PointIndex:
    anchors, labels = _checkpoint_maps(result)
    return PointIndex(
        {
            polygon_id: _validated_anchor(polygon_id, coordinates, labels)
            for polygon_id, coordinates in anchors.items()
        }
    )


def _checkpoint_maps(result: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    anchors = result.get("eligible_polygon_anchors")
    labels = result.get("eligible_polygon_labels")
    if not isinstance(anchors, dict):
        raise ValueError("point checkpoint is missing eligible polygon anchors")
    if not isinstance(labels, dict) or set(labels) != set(anchors):
        raise ValueError("point checkpoint polygon labels and anchors disagree")
    return anchors, labels


def _validated_anchor(
    polygon_id: Any, coordinates: Any, labels: Mapping[str, Any]
) -> tuple[float, float]:
    if not isinstance(polygon_id, str) or not polygon_id:
        raise ValueError("point checkpoint contains invalid polygon anchors")
    normalized = _coordinate_pair(coordinates)
    _validate_anchor_bounds(normalized)
    _validate_polygon_label(labels, polygon_id)
    return normalized


def _coordinate_pair(value: Any) -> tuple[float, float]:
    if not _is_coordinate_pair(value):
        raise ValueError("point checkpoint contains invalid polygon anchors")
    longitude, latitude = float(value[0]), float(value[1])
    if not math.isfinite(longitude) or not math.isfinite(latitude):
        raise ValueError("point checkpoint contains non-finite polygon anchors")
    return longitude, latitude


def _is_coordinate_pair(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(_is_coordinate_number(item) for item in value)
    )


def _is_coordinate_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int | float)


def _validate_anchor_bounds(coordinates: tuple[float, float]) -> None:
    longitude, latitude = coordinates
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise ValueError("point checkpoint anchor is outside OGC:CRS84 bounds")


def _validate_polygon_label(labels: Mapping[str, Any], polygon_id: str) -> None:
    if not isinstance(labels[polygon_id], str) or not labels[polygon_id]:
        raise ValueError("point checkpoint contains an invalid polygon label")


def key_from_row(row: Mapping[str, Any], fields: Sequence[str]) -> tuple[Any, ...]:
    values = []
    for field in fields:
        value = row.get(field)
        if field == "sentence_index":
            _validate_sentence_index(value)
        else:
            _validate_text_key(value, field)
        values.append(value)
    return tuple(values)


def _validate_sentence_index(value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("sentence_index must be a non-negative integer")


def _validate_text_key(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be non-empty text")


def _validated_links(row: Mapping[str, Any]) -> list[str]:
    links = row.get("polygon_ids")
    if not isinstance(links, list) or any(
        not isinstance(polygon_id, str) or not polygon_id for polygon_id in links
    ):
        raise ValueError("source text unit polygon_ids must be non-empty strings")
    return links


def _candidate_link(
    row: Mapping[str, Any],
    key_fields: Sequence[str],
    source_polygon_ids: set[str],
    eligible_polygon_ids: set[str],
    source_id: str,
) -> tuple[tuple[Any, ...], list[str]] | None:
    key = key_from_row(row, key_fields)
    links = _validated_links(row)
    unknown = set(links) - source_polygon_ids
    if unknown:
        raise ValueError(
            f"{source_id} text unit references unknown polygons: {sorted(unknown)[:5]}"
        )
    if not set(links).intersection(eligible_polygon_ids):
        return None
    return key, sorted(set(links))


def collect_candidate_links(
    rows: Iterable[Mapping[str, Any]],
    key_fields: Sequence[str],
    source_polygon_ids: set[str],
    eligible_polygon_ids: set[str],
    source_id: str,
) -> tuple[dict[tuple[Any, ...], list[str]], int]:
    candidate_links: dict[tuple[Any, ...], list[str]] = {}
    row_count = 0
    for row in rows:
        row_count += 1
        candidate = _candidate_link(
            row,
            key_fields,
            source_polygon_ids,
            eligible_polygon_ids,
            source_id,
        )
        if candidate is None:
            continue
        key, links = candidate
        if key in candidate_links:
            raise ValueError("duplicate point-eligible source text-unit key")
        candidate_links[key] = links
    return candidate_links, row_count


def validate_text_key_replay(
    rows: Iterable[Mapping[str, Any]],
    key_fields: Sequence[str],
    candidate_keys: set[tuple[Any, ...]],
    expected_rows: int,
) -> int:
    seen, row_count = _replayed_candidate_keys(rows, key_fields, candidate_keys)
    if row_count != expected_rows:
        raise ValueError("source text units changed during duplicate-key validation")
    if seen != candidate_keys:
        raise ValueError("eligible source text key was not re-read")
    return row_count


def _replayed_candidate_keys(
    rows: Iterable[Mapping[str, Any]],
    key_fields: Sequence[str],
    candidate_keys: set[tuple[Any, ...]],
) -> tuple[set[tuple[Any, ...]], int]:
    seen: set[tuple[Any, ...]] = set()
    row_count = 0
    for row in rows:
        row_count += 1
        key = key_from_row(row, key_fields)
        if key not in candidate_keys:
            continue
        if key in seen:
            raise ValueError("duplicate point-eligible source text-unit key")
        seen.add(key)
    return seen, row_count


def _label_fields(
    row: Mapping[str, Any], key_fields: Sequence[str]
) -> tuple[tuple[Any, ...], str, str]:
    status = row.get("status")
    revision = row.get("input_revision")
    if status not in _LABEL_STATUSES:
        raise ValueError(f"source label has unsupported status: {status!r}")
    if not isinstance(revision, str):
        raise ValueError("source label input_revision must be text")
    return key_from_row(row, key_fields), status, revision


def _record_label(
    key: tuple[Any, ...],
    status: str,
    candidate_keys: set[tuple[Any, ...]],
    labels: dict[tuple[Any, ...], str],
) -> None:
    if key not in candidate_keys:
        return
    if key in labels:
        raise ValueError("duplicate point-eligible source label key")
    labels[key] = status


def _validate_label_join(
    row_count: int,
    expected_rows: int,
    source_statuses: Counter[str],
    revisions: set[str],
    expected_revision: str,
    labels: Mapping[tuple[Any, ...], str],
    candidate_keys: set[tuple[Any, ...]],
) -> None:
    if row_count != expected_rows:
        raise ValueError("source label row count differs from Parquet metadata")
    if source_statuses and revisions != {expected_revision}:
        raise ValueError("source labels do not use the pinned input revision")
    if set(labels) != candidate_keys:
        raise ValueError("point-eligible source text units are missing pinned labels")


def join_candidate_labels(
    rows: Iterable[Mapping[str, Any]],
    key_fields: Sequence[str],
    candidate_keys: set[tuple[Any, ...]],
    expected_revision: str,
    expected_rows: int,
) -> tuple[dict[tuple[Any, ...], str], Counter[str], Counter[str]]:
    source_statuses: Counter[str] = Counter()
    joined_statuses: Counter[str] = Counter()
    revisions: set[str] = set()
    labels: dict[tuple[Any, ...], str] = {}
    row_count = 0
    for row in rows:
        row_count += 1
        key, status, revision = _label_fields(row, key_fields)
        source_statuses[status] += 1
        revisions.add(revision)
        _record_label(key, status, candidate_keys, labels)
        if key in candidate_keys:
            joined_statuses[status] += 1
    _validate_label_join(
        row_count,
        expected_rows,
        source_statuses,
        revisions,
        expected_revision,
        labels,
        candidate_keys,
    )
    return labels, source_statuses, joined_statuses


def make_candidate_rows(
    source_id: str,
    stream_name: str,
    region: str,
    candidate_links: Mapping[tuple[Any, ...], list[str]],
    labels: Mapping[tuple[Any, ...], str],
) -> list[dict[str, Any]]:
    return [
        {
            "sentence_id": canonical_json([source_id, stream_name, list(key)]),
            "sentence_key": list(key),
            "status": labels[key],
            "polygon_ids": candidate_links[key],
            "origin_region": region,
        }
        for key in sorted(candidate_links)
    ]


def candidate_partition_report(
    region: str,
    stream_name: str,
    text_rows: int,
    duplicate_validation_rows: int,
    source_statuses: Counter[str],
    joined_statuses: Counter[str],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "region": region,
        "stream": stream_name,
        "text_unit_rows_scanned": text_rows,
        "duplicate_validation_rows": duplicate_validation_rows,
        "source_label_rows": dict(sorted(source_statuses.items())),
        "joined_to_point_eligible_polygons": dict(sorted(joined_statuses.items())),
        "not_joined_to_point_eligible_polygons": {
            status: count - joined_statuses[status]
            for status, count in sorted(source_statuses.items())
        },
        "point_eligible_sentence_instances": len(candidates),
        "candidates": candidates,
    }


def _candidate_signature(row: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
    return row["status"], tuple(row["sentence_key"])


def _merge_partition_row(
    row: dict[str, Any],
    seen: dict[str, tuple[str, tuple[str, ...]]],
    metadata: dict[str, dict[str, Any]],
    allow_link_union: bool,
) -> bool:
    sentence_id = row["sentence_id"]
    signature = _candidate_signature(row)
    previous = seen.get(sentence_id)
    if previous is None:
        seen[sentence_id] = signature
        metadata[sentence_id] = {
            **row,
            "polygon_ids": sorted(set(row["polygon_ids"])),
        }
        return False
    if previous != signature:
        raise ValueError("repeated sentence key has conflicting labels or keys")
    _merge_polygon_links(metadata[sentence_id], row, allow_link_union)
    return True


def _merge_polygon_links(
    previous: dict[str, Any], row: Mapping[str, Any], allow_link_union: bool
) -> None:
    previous_links = set(previous["polygon_ids"])
    repeated_links = set(row["polygon_ids"])
    if previous_links != repeated_links and not allow_link_union:
        raise ValueError("repeated sentence key has conflicting polygon links")
    previous["polygon_ids"] = sorted(previous_links | repeated_links)
    previous["origin_region"] = min(previous["origin_region"], row["origin_region"])


def coalesce_partition_candidates(
    schema: str,
    partition_results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], int]:
    allow_link_union = schema == "wiki-document-sentences-v1"
    seen: dict[str, tuple[str, tuple[str, ...]]] = {}
    metadata: dict[str, dict[str, Any]] = {}
    repeated_rows = 0
    for result in partition_results:
        repeated_rows += _merge_partition_rows(
            result["candidates"], seen, metadata, allow_link_union
        )
    candidates = [
        {
            "sentence_id": sentence_id,
            "status": row["status"],
            "polygon_ids": row["polygon_ids"],
        }
        for sentence_id, row in sorted(metadata.items())
    ]
    return candidates, metadata, repeated_rows


def _merge_partition_rows(
    rows: Iterable[dict[str, Any]],
    seen: dict[str, tuple[str, tuple[str, ...]]],
    metadata: dict[str, dict[str, Any]],
    allow_link_union: bool,
) -> int:
    repeated = 0
    for row in rows:
        repeated += _merge_partition_row(row, seen, metadata, allow_link_union)
    return repeated


def sample_report(sample: PointSentenceSample) -> dict[str, Any]:
    return {
        "source": sample.source,
        "seed": sample.seed,
        "target_per_label": sample.target_per_label,
        "h3_resolution": sample.h3_resolution,
        "h3_version": sample.h3_version,
        "max_per_cell": sample.max_per_cell,
        "eligible_by_label": dict(sample.eligible_by_label),
        "selected_by_label": dict(sample.selected_by_label),
        "shortfall_by_label": dict(sample.shortfall_by_label),
        "unique_eligible_polygons": sample.unique_eligible_polygons,
        "unique_selected_polygons": sample.unique_selected_polygons,
        "occupied_cells": sample.occupied_cells,
        "selected_cells": sample.selected_cells,
        "repeated_source_rows_skipped": sample.repeated_source_rows_skipped,
        "multi_polygon_sentences_anchored": sample.multi_polygon_sentences_anchored,
        "non_binary_statuses": dict(sample.non_binary_statuses),
        "selected_sentence_ids_by_polygon": {
            key: list(value)
            for key, value in sorted(sample.selected_sentence_ids_by_polygon.items())
        },
        "selected": [
            {
                "sentence_id": entry.sentence_id,
                "relevance": entry.relevance,
                "anchor_polygon_id": entry.anchor_polygon_id,
                "cell": entry.cell,
            }
            for entry in sample.selected
        ],
    }


def annotate_selected(
    selected: list[dict[str, Any]],
    metadata: Mapping[str, dict[str, Any]],
    join_key_fields: Sequence[str],
) -> None:
    for row in selected:
        candidate = metadata[row["sentence_id"]]
        row["natural_key"] = dict(
            zip(join_key_fields, candidate["sentence_key"], strict=True)
        )
        row["origin_region"] = candidate["origin_region"]


def partition_receipts(partition_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in result.items() if key != "candidates"}
        for result in partition_results
    ]


def sample_cohort(
    *,
    source_name: str,
    source: dict[str, Any],
    stream_name: str,
    partition_results: list[dict[str, Any]],
    point_index: PointIndex,
    target_per_label: int,
    seed: int,
    h3_resolution: int,
    max_per_cell: int,
) -> dict[str, Any]:
    candidates, metadata, repeated_rows = coalesce_partition_candidates(
        source["direct_input"]["schema"], partition_results
    )
    sample = sample_point_sentence_instances(
        candidates,
        point_index,
        source=f"{source['source_id']}/{stream_name}",
        target_per_label=target_per_label,
        seed=seed,
        h3_resolution=h3_resolution,
        max_per_cell=max_per_cell,
    )
    report = sample_report(sample)
    annotate_selected(report["selected"], metadata, source["join_key_fields"])
    return _cohort_report(
        source_name,
        source,
        stream_name,
        partition_results,
        repeated_rows,
        metadata,
        report,
    )


def sample_combined_wiki_cohort(
    *,
    source_name: str,
    source: dict[str, Any],
    partition_results_by_stream: Mapping[str, list[dict[str, Any]]],
    point_index: PointIndex,
    target_per_label: int,
    seed: int,
    h3_resolution: int,
    max_per_cell: int,
) -> dict[str, Any]:
    """Coalesce Wiki streams by source key, then sample them as one cohort."""
    if not partition_results_by_stream:
        raise ValueError("combined Wiki sampling requires at least one stream")
    source_id = source["source_id"]
    (
        candidates,
        metadata,
        stream_summaries,
        partition_results,
        repeated_partition_rows,
        repeated_cross_stream_rows,
    ) = _coalesce_wiki_streams(source, partition_results_by_stream)
    sample = sample_point_sentence_instances(
        candidates,
        point_index,
        source=f"{source_id}/combined",
        target_per_label=target_per_label,
        seed=seed,
        h3_resolution=h3_resolution,
        max_per_cell=max_per_cell,
    )
    report = sample_report(sample)
    _annotate_combined_wiki_selection(report["selected"], metadata, source)

    return {
        "source_name": source_name,
        "source_id": source_id,
        "stream": _wiki_cohort_stream_label(stream_summaries),
        "streams": stream_summaries,
        "partition_results": partition_receipts(partition_results),
        "point_eligible_sentence_instances_by_status": dict(
            sorted(Counter(row["status"] for row in metadata.values()).items())
        ),
        "repeated_partition_sentence_rows_coalesced": repeated_partition_rows,
        "repeated_cross_stream_sentence_rows_coalesced": repeated_cross_stream_rows,
        "sample": report,
        "selected": report["selected"],
        "selected_manifest": {
            "key_fields": list(source["join_key_fields"]),
            "entries": report["selected"],
        },
    }


def _coalesce_wiki_streams(
    source: dict[str, Any],
    partition_results_by_stream: Mapping[str, list[dict[str, Any]]],
) -> tuple[
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    int,
    int,
]:
    stream_summaries: list[dict[str, Any]] = []
    partition_results: list[dict[str, Any]] = []
    candidates_by_id: dict[str, dict[str, Any]] = {}
    metadata: dict[str, dict[str, Any]] = {}
    repeated_partition_rows = 0
    repeated_cross_stream_rows = 0

    for stream_name, stream_partitions in partition_results_by_stream.items():
        _, stream_metadata, repeated_rows = coalesce_partition_candidates(
            source["direct_input"]["schema"], stream_partitions
        )
        repeated_partition_rows += repeated_rows
        partition_results.extend(stream_partitions)
        stream_summaries.append(
            {
                "stream": stream_name,
                "point_eligible_sentence_instances_by_status": dict(
                    sorted(
                        Counter(
                            row["status"] for row in stream_metadata.values()
                        ).items()
                    )
                ),
                "repeated_partition_sentence_rows_coalesced": repeated_rows,
            }
        )

        for row in stream_metadata.values():
            repeated_cross_stream_rows += _merge_wiki_stream_row(
                source["source_id"],
                stream_name,
                row,
                candidates_by_id,
                metadata,
            )

    candidates = [candidates_by_id[key] for key in sorted(candidates_by_id)]
    return (
        candidates,
        metadata,
        stream_summaries,
        partition_results,
        repeated_partition_rows,
        repeated_cross_stream_rows,
    )


def _merge_wiki_stream_row(
    source_id: str,
    stream_name: str,
    row: dict[str, Any],
    candidates: dict[str, dict[str, Any]],
    metadata: dict[str, dict[str, Any]],
) -> int:
    sentence_id = canonical_json([source_id, row["sentence_key"]])
    previous = metadata.get(sentence_id)
    if previous is None:
        merged = {
            **row,
            "sentence_id": sentence_id,
            "polygon_ids": sorted(set(row["polygon_ids"])),
            "origin_streams": {stream_name},
        }
        metadata[sentence_id] = merged
        candidates[sentence_id] = {
            "sentence_id": sentence_id,
            "status": row["status"],
            "polygon_ids": merged["polygon_ids"],
        }
        return 0
    _merge_existing_wiki_stream_row(previous, row, stream_name)
    candidates[sentence_id]["polygon_ids"] = previous["polygon_ids"]
    return 1


def _merge_existing_wiki_stream_row(
    previous: dict[str, Any], row: dict[str, Any], stream_name: str
) -> None:
    if previous["status"] != row["status"]:
        raise ValueError("Wiki streams contain conflicting sentence labels")
    previous["polygon_ids"] = sorted(
        set(previous["polygon_ids"]) | set(row["polygon_ids"])
    )
    previous["origin_region"] = min(previous["origin_region"], row["origin_region"])
    previous["origin_streams"].add(stream_name)


def _annotate_combined_wiki_selection(
    selected: list[dict[str, Any]],
    metadata: Mapping[str, dict[str, Any]],
    source: dict[str, Any],
) -> None:
    for row in selected:
        candidate = metadata[row["sentence_id"]]
        row["natural_key"] = dict(
            zip(source["join_key_fields"], candidate["sentence_key"], strict=True)
        )
        row["origin_region"] = candidate["origin_region"]
        row["origin_streams"] = sorted(candidate["origin_streams"])


def _wiki_cohort_stream_label(stream_summaries: list[dict[str, Any]]) -> str:
    if len(stream_summaries) == 1:
        return stream_summaries[0]["stream"]
    return "combined"


def _cohort_report(
    source_name: str,
    source: dict[str, Any],
    stream_name: str,
    partition_results: list[dict[str, Any]],
    repeated_rows: int,
    metadata: Mapping[str, dict[str, Any]],
    sample: dict[str, Any],
) -> dict[str, Any]:
    selected = sample["selected"]
    return {
        "source_name": source_name,
        "source_id": source["source_id"],
        "stream": stream_name,
        "partition_results": partition_receipts(partition_results),
        "point_eligible_sentence_instances_by_status": dict(
            sorted(Counter(row["status"] for row in metadata.values()).items())
        ),
        "repeated_partition_sentence_rows_coalesced": repeated_rows,
        "sample": sample,
        "selected": selected,
        "selected_manifest": {
            "key_fields": list(source["join_key_fields"]),
            "entries": selected,
        },
    }
