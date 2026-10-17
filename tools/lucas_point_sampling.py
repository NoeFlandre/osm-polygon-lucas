"""Deterministic geographic sampling of sentences linked to point-eligible polygons."""

from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pyproj import Transformer

from osm_polygon_lucas.point_coverage import PointCensus

_CELL_TRANSFORMER = Transformer.from_crs("OGC:CRS84", "EPSG:3035", always_xy=True)
_LABELS = ("yes", "no")


@dataclass(frozen=True, slots=True)
class PointSentenceSampleEntry:
    sentence_id: str
    relevance: str
    anchor_polygon_id: str
    cell: tuple[int, int]


@dataclass(frozen=True, slots=True)
class PointSentenceSample:
    source: str
    seed: int
    target_per_label: int
    cell_size_metres: float
    max_per_cell: int
    eligible_by_label: Mapping[str, int]
    selected_by_label: Mapping[str, int]
    shortfall_by_label: Mapping[str, int]
    unique_eligible_polygons: int
    unique_selected_polygons: int
    occupied_cells: int
    selected_cells: int
    repeated_source_rows_skipped: int
    multi_polygon_sentences_anchored: int
    non_binary_statuses: Mapping[str, int]
    selected: tuple[PointSentenceSampleEntry, ...]
    selected_sentence_ids_by_polygon: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class _Candidate:
    sentence_id: str
    relevance: str
    polygon_id: str
    cell: tuple[int, int]
    rank: str


def sample_point_sentence_instances(
    candidates: Iterable[Mapping[str, Any]],
    point_census: PointCensus,
    *,
    source: str,
    target_per_label: int = 500,
    seed: int = 42,
    cell_size_metres: float = 50_000,
    max_per_cell: int = 20,
) -> PointSentenceSample:
    """Select a balanced, geographically spread sample without sentence text.

    Each source sentence key is counted once, even when linked to several
    eligible polygons. Its geographic anchor is the lexically smallest eligible
    polygon key. Repeated sentences remain grouped by polygon in the returned
    manifest so downstream summaries can use the polygon as the cluster.
    """
    _validate_sampling_options(source, target_per_label, cell_size_metres, max_per_cell)
    anchors = point_census.eligible_polygon_anchors
    unique_candidates, repeated_rows, non_binary = _candidate_rows(candidates)
    eligible, eligible_polygon_ids, multi_polygon_sentences = _eligible_candidates(
        unique_candidates,
        anchors,
        source=source,
        seed=seed,
        cell_size_metres=cell_size_metres,
    )
    selected, occupied_cells = _select_candidates(
        eligible,
        seed=seed,
        source=source,
        target_per_label=target_per_label,
        max_per_cell=max_per_cell,
    )
    return _build_sample(
        source=source,
        seed=seed,
        target_per_label=target_per_label,
        cell_size_metres=cell_size_metres,
        max_per_cell=max_per_cell,
        eligible=eligible,
        eligible_polygon_ids=eligible_polygon_ids,
        selected=selected,
        occupied_cells=occupied_cells,
        repeated_rows=repeated_rows,
        multi_polygon_sentences=multi_polygon_sentences,
        non_binary=non_binary,
    )


def _eligible_candidates(
    unique_candidates: list[tuple[str, str, tuple[str, ...]]],
    anchors: Mapping[str, tuple[float, float]],
    *,
    source: str,
    seed: int,
    cell_size_metres: float,
) -> tuple[dict[str, list[_Candidate]], set[str], int]:
    eligible: dict[str, list[_Candidate]] = {label: [] for label in _LABELS}
    eligible_polygon_ids: set[str] = set()
    multi_polygon_sentences = 0
    for sentence_id, relevance, polygon_ids in unique_candidates:
        linked = sorted(set(polygon_ids).intersection(anchors))
        if not linked:
            continue
        eligible_polygon_ids.update(linked)
        if len(linked) > 1:
            multi_polygon_sentences += 1
        anchor_id = linked[0]
        longitude, latitude = anchors[anchor_id]
        easting, northing = _project(longitude, latitude)
        cell = (
            math.floor(easting / cell_size_metres),
            math.floor(northing / cell_size_metres),
        )
        eligible[relevance].append(
            _Candidate(
                sentence_id=sentence_id,
                relevance=relevance,
                polygon_id=anchor_id,
                cell=cell,
                rank=_rank(seed, source, sentence_id),
            )
        )
    return eligible, eligible_polygon_ids, multi_polygon_sentences


def _build_sample(
    *,
    source: str,
    seed: int,
    target_per_label: int,
    cell_size_metres: float,
    max_per_cell: int,
    eligible: Mapping[str, list[_Candidate]],
    eligible_polygon_ids: set[str],
    selected: list[PointSentenceSampleEntry],
    occupied_cells: int,
    repeated_rows: int,
    multi_polygon_sentences: int,
    non_binary: Counter[str],
) -> PointSentenceSample:
    selected_counts, ids_by_polygon, frozen_groups = _selected_groups(selected)
    selected_counts_by_label = _label_counts(selected_counts)
    eligible_counts = _label_counts(
        Counter({label: len(eligible[label]) for label in _LABELS})
    )
    return PointSentenceSample(
        source=source,
        seed=seed,
        target_per_label=target_per_label,
        cell_size_metres=cell_size_metres,
        max_per_cell=max_per_cell,
        eligible_by_label=MappingProxyType(eligible_counts),
        selected_by_label=MappingProxyType(selected_counts_by_label),
        shortfall_by_label=MappingProxyType(
            {
                label: max(0, target_per_label - selected_counts[label])
                for label in _LABELS
            }
        ),
        unique_eligible_polygons=len(eligible_polygon_ids),
        unique_selected_polygons=len(ids_by_polygon),
        occupied_cells=occupied_cells,
        selected_cells=len({entry.cell for entry in selected}),
        repeated_source_rows_skipped=repeated_rows,
        multi_polygon_sentences_anchored=multi_polygon_sentences,
        non_binary_statuses=MappingProxyType(dict(sorted(non_binary.items()))),
        selected=tuple(selected),
        selected_sentence_ids_by_polygon=frozen_groups,
    )


def _selected_groups(
    selected: list[PointSentenceSampleEntry],
) -> tuple[Counter[str], dict[str, list[str]], Mapping[str, tuple[str, ...]]]:
    selected_counts = Counter(entry.relevance for entry in selected)
    ids_by_polygon: dict[str, list[str]] = defaultdict(list)
    for entry in selected:
        ids_by_polygon[entry.anchor_polygon_id].append(entry.sentence_id)
    frozen_groups = MappingProxyType(
        {
            polygon_id: tuple(sorted(sentence_ids))
            for polygon_id, sentence_ids in sorted(ids_by_polygon.items())
        }
    )
    return selected_counts, ids_by_polygon, frozen_groups


def _label_counts(counts: Counter[str]) -> dict[str, int]:
    return {label: counts[label] for label in _LABELS}


def _validate_sampling_options(
    source: str, target_per_label: int, cell_size_metres: float, max_per_cell: int
) -> None:
    if not isinstance(source, str) or not source:
        raise ValueError("source must be non-empty text")
    _validate_positive_integer(target_per_label, "target_per_label")
    _validate_positive_integer(max_per_cell, "max_per_cell")
    _validate_cell_size(cell_size_metres)


def _validate_positive_integer(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _validate_cell_size(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("cell_size_metres must be a positive finite number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("cell_size_metres must be a positive finite number")


def _candidate_rows(
    candidates: Iterable[Mapping[str, Any]],
) -> tuple[list[tuple[str, str, tuple[str, ...]]], int, Counter[str]]:
    seen: dict[str, tuple[str, tuple[str, ...]]] = {}
    repeated_rows = 0
    non_binary: Counter[str] = Counter()
    for row in candidates:
        sentence_id, relevance, polygon_ids = _parse_candidate_row(row)
        repeated_rows += _remember_candidate(seen, sentence_id, relevance, polygon_ids)
        if relevance not in _LABELS:
            non_binary[relevance] += 1
    return (
        [
            (sentence_id, relevance, polygon_ids)
            for sentence_id, (relevance, polygon_ids) in sorted(seen.items())
            if relevance in _LABELS
        ],
        repeated_rows,
        non_binary,
    )


def _parse_candidate_row(row: Mapping[str, Any]) -> tuple[str, str, tuple[str, ...]]:
    sentence_id = _required_nonempty_text(row.get("sentence_id"), "sentence_id")
    relevance = _required_nonempty_text(row.get("status"), "source relevance status")
    polygon_ids = row.get("polygon_ids")
    if not isinstance(polygon_ids, list | tuple):
        raise ValueError("polygon_ids must be a sequence of non-empty strings")
    canonical_polygon_ids = _canonical_polygon_ids(polygon_ids)
    return sentence_id, relevance, canonical_polygon_ids


def _required_nonempty_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty text")
    return value


def _canonical_polygon_ids(polygon_ids: list[Any] | tuple[Any, ...]) -> tuple[str, ...]:
    canonical: set[str] = set()
    for polygon_id in polygon_ids:
        if not isinstance(polygon_id, str) or not polygon_id:
            raise ValueError("polygon_ids must be a sequence of non-empty strings")
        canonical.add(polygon_id)
    return tuple(sorted(canonical))


def _remember_candidate(
    seen: dict[str, tuple[str, tuple[str, ...]]],
    sentence_id: str,
    relevance: str,
    polygon_ids: tuple[str, ...],
) -> int:
    canonical = (relevance, polygon_ids)
    previous = seen.get(sentence_id)
    if previous is None:
        seen[sentence_id] = canonical
        return 0
    if previous != canonical:
        raise ValueError("duplicate sentence_id has conflicting source rows")
    return 1


def _project(longitude: float, latitude: float) -> tuple[float, float]:
    try:
        easting, northing = _CELL_TRANSFORMER.transform(longitude, latitude)
    except Exception as exc:
        raise ValueError(
            "eligible polygon anchor cannot be projected to EPSG:3035"
        ) from exc
    if not math.isfinite(easting) or not math.isfinite(northing):
        raise ValueError("eligible polygon anchor projects to non-finite coordinates")
    return easting, northing


def _rank(seed: int, source: str, sentence_id: str) -> str:
    payload = f"{seed}\0{source}\0{sentence_id}".encode()
    return hashlib.sha256(payload).hexdigest()


def _select_candidates(
    eligible: Mapping[str, list[_Candidate]],
    *,
    seed: int,
    source: str,
    target_per_label: int,
    max_per_cell: int,
) -> tuple[list[PointSentenceSampleEntry], int]:
    cells_by_label, queues, occupied = _candidate_queues(eligible, seed, source)
    return _round_robin_select(
        cells_by_label,
        queues,
        target_per_label=target_per_label,
        max_per_cell=max_per_cell,
    ), len(occupied)


def _candidate_queues(
    eligible: Mapping[str, list[_Candidate]], seed: int, source: str
) -> tuple[
    dict[str, list[tuple[int, int]]],
    dict[str, dict[tuple[int, int], deque[_Candidate]]],
    set[tuple[int, int]],
]:
    cells_by_label: dict[str, list[tuple[int, int]]] = {}
    queues: dict[str, dict[tuple[int, int], deque[_Candidate]]] = {}
    occupied: set[tuple[int, int]] = set()
    for label in _LABELS:
        grouped: dict[tuple[int, int], list[_Candidate]] = defaultdict(list)
        for candidate in eligible[label]:
            grouped[candidate.cell].append(candidate)
            occupied.add(candidate.cell)
        queues[label] = {
            cell: deque(sorted(items, key=lambda item: item.rank))
            for cell, items in grouped.items()
        }
        cells_by_label[label] = sorted(
            grouped,
            key=lambda cell: _rank(seed, source, f"cell:{cell[0]}:{cell[1]}"),
        )
    return cells_by_label, queues, occupied


def _round_robin_select(
    cells_by_label: Mapping[str, list[tuple[int, int]]],
    queues: Mapping[str, dict[tuple[int, int], deque[_Candidate]]],
    *,
    target_per_label: int,
    max_per_cell: int,
) -> list[PointSentenceSampleEntry]:
    cell_counts: Counter[tuple[int, int]] = Counter()
    selected_counts: Counter[str] = Counter()
    cursors = {label: 0 for label in _LABELS}
    selected: list[PointSentenceSampleEntry] = []
    while _needs_samples(selected_counts, target_per_label):
        progress = _select_sample_round(
            cells_by_label,
            queues,
            cursors,
            cell_counts,
            selected_counts,
            selected,
            target_per_label=target_per_label,
            max_per_cell=max_per_cell,
        )
        if not progress:
            break
    return selected


def _needs_samples(selected_counts: Counter[str], target_per_label: int) -> bool:
    return any(selected_counts[label] < target_per_label for label in _LABELS)


def _select_sample_round(
    cells_by_label: Mapping[str, list[tuple[int, int]]],
    queues: Mapping[str, dict[tuple[int, int], deque[_Candidate]]],
    cursors: dict[str, int],
    cell_counts: Counter[tuple[int, int]],
    selected_counts: Counter[str],
    selected: list[PointSentenceSampleEntry],
    *,
    target_per_label: int,
    max_per_cell: int,
) -> bool:
    progress = False
    for label in _LABELS:
        if selected_counts[label] >= target_per_label:
            continue
        candidate = _next_candidate(
            label,
            cells_by_label,
            queues,
            cursors,
            cell_counts,
            max_per_cell,
        )
        if candidate is None:
            continue
        _record_selected_candidate(
            candidate, label, cell_counts, selected_counts, selected
        )
        progress = True
    return progress


def _record_selected_candidate(
    candidate: _Candidate,
    label: str,
    cell_counts: Counter[tuple[int, int]],
    selected_counts: Counter[str],
    selected: list[PointSentenceSampleEntry],
) -> None:
    selected.append(
        PointSentenceSampleEntry(
            sentence_id=candidate.sentence_id,
            relevance=label,
            anchor_polygon_id=candidate.polygon_id,
            cell=candidate.cell,
        )
    )
    selected_counts[label] += 1
    cell_counts[candidate.cell] += 1


def _next_candidate(
    label: str,
    cells_by_label: Mapping[str, list[tuple[int, int]]],
    queues: Mapping[str, dict[tuple[int, int], deque[_Candidate]]],
    cursors: dict[str, int],
    cell_counts: Counter[tuple[int, int]],
    max_per_cell: int,
) -> _Candidate | None:
    cells = cells_by_label[label]
    if not cells:
        return None
    start = cursors[label] % len(cells)
    for offset in range(len(cells)):
        index = (start + offset) % len(cells)
        cell = cells[index]
        queue = queues[label][cell]
        cursors[label] = (index + 1) % len(cells)
        if cell_counts[cell] >= max_per_cell or not queue:
            continue
        return queue.popleft()
    return None
