# Mutation triage

## Current hosted mutation survivors

The mutation job compares every surviving mutant ID against this table. An undocumented, stale, or duplicate ID fails the job. Each listed case has a behavior-equivalence or domain-based rationale.

| Mutant ID | Reason |
| --- | --- |
| `tools.lucas_point_census_domain.x_sample_combined_wiki_cohort__mutmut_3` | The mutation only changes the wording of the empty-stream `ValueError`; the same input still fails at the same boundary, and exact diagnostic wording is not a public contract. |
| `tools.lucas_point_census_domain.x_sample_combined_wiki_cohort__mutmut_4` | The mutation only changes capitalization in the empty-stream `ValueError`; the same input still fails at the same boundary, and exact diagnostic capitalization is not a public contract. |
| `tools.lucas_point_census_domain.x__merge_existing_wiki_stream_row__mutmut_7` | The mutation only changes the conflicting-label `ValueError` wording; label conflicts still fail at the same boundary, and exact diagnostic wording is not a public contract. |
| `tools.lucas_point_census_domain.x__merge_existing_wiki_stream_row__mutmut_8` | The mutation only changes capitalization in the conflicting-label `ValueError`; label conflicts still fail at the same boundary, and exact diagnostic capitalization is not a public contract. |
| `tools.lucas_point_census_domain.x__merge_wiki_stream_row__mutmut_9` | The metadata mapping is keyed by the canonical sentence ID, and the annotator reads the natural key, region, and provenance from its value; the value's redundant `sentence_id` is never read. |
| `tools.lucas_point_census_domain.x__merge_wiki_stream_row__mutmut_10` | The metadata mapping is keyed by the canonical sentence ID, and the annotator reads the natural key, region, and provenance from its value; the value's redundant `sentence_id` is never read. |
| `tools.lucas_point_census_domain.x__merge_wiki_stream_row__mutmut_11` | The original row already supplies the consumed `polygon_ids` field; this mutation only adds an unused misspelled alias to the metadata dictionary. |
| `tools.lucas_point_census_domain.x__merge_wiki_stream_row__mutmut_12` | The original row already supplies the consumed `polygon_ids` field; this mutation only adds an unused uppercase alias to the metadata dictionary. |
| `osm_polygon_lucas.cli.x__report_text__mutmut_30` | `None` and `False` are both falsey. `json.dumps` rejects non-finite numbers in either case. |
| `osm_polygon_lucas.cli.x__read_osm_wkt__mutmut_10` | Python resolves `UTF-8` and `utf-8` to the same codec. |
| `osm_polygon_lucas.cli.x__source_manifest__mutmut_11` | Python resolves `UTF-8` and `utf-8` to the same codec. |
| `osm_polygon_lucas.cli.x__create_temporary_directory__mutmut_8` | The uppercase prefix retains the leading dot and keeps the directory unique beneath the output parent; prefix case does not affect collision checking, cleanup, or report behavior. |
| `osm_polygon_lucas.lucas_gpkg.x__check_layer__mutmut_12` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__check_layer__mutmut_13` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__check_layer__mutmut_38` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__check_layer__mutmut_39` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__check_layer__mutmut_41` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__check_crs__mutmut_11` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__check_crs__mutmut_12` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__check_crs__mutmut_14` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__check_crs__mutmut_15` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__check_rtree__mutmut_10` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__select_rows__mutmut_10` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__select_rows__mutmut_13` | SQLite resolves SQL keywords and unquoted identifiers without regard to case. Bound values are unchanged. |
| `osm_polygon_lucas.lucas_gpkg.x__wkb_order__mutmut_14` | For supported WKB types 1-7, byte 1 is nonzero for little-endian and zero for big-endian, so this test selects the same byte order as the marker at byte 0. |
| `osm_polygon_lucas.support_eligibility.x_classify_support_row__mutmut_67` | A non-string support class adds a rejection reason, and a missing polygon adds a geometry reason. The final type and polygon checks cannot change the result after those checks. |
| `osm_polygon_lucas.polygon_coverage.x__candidate_overlap_pairs__mutmut_11` | For array input, Shapely returns a `(2, n)` index matrix. Both `zip` inputs therefore have length `n`, so changing `strict` cannot change valid query results. |
| `osm_polygon_lucas.polygon_coverage.x__candidate_overlap_pairs__mutmut_14` | For array input, Shapely returns a `(2, n)` index matrix. Both `zip` inputs therefore have length `n`, so changing `strict` cannot change valid query results. |
| `osm_polygon_lucas.polygon_coverage.x__candidate_overlap_pairs__mutmut_17` | For array input, Shapely returns a `(2, n)` index matrix. Both `zip` inputs therefore have length `n`, so changing `strict` cannot change valid query results. |
| `osm_polygon_lucas.lucas_point_gpkg.x_iter_lucas_2022_point_observations__mutmut_11` | SQLite treats `fid` and `FID` as the same unquoted column identifier. |
| `osm_polygon_lucas.lucas_point_gpkg.x_iter_lucas_2022_point_observations__mutmut_18` | The SELECT projection and `_POINT_FIELDS` have the same fixed number of columns, and `_check_point_fields` rejects missing fields before iteration; disabling strict zip cannot change row mapping for this query. |
| `osm_polygon_lucas.lucas_point_gpkg.x_iter_lucas_2022_point_observations__mutmut_21` | The SELECT projection and `_POINT_FIELDS` have the same fixed number of columns, and `_check_point_fields` rejects missing fields before iteration; disabling strict zip cannot change row mapping for this query. |
| `osm_polygon_lucas.lucas_point_gpkg.x_iter_lucas_2022_point_observations__mutmut_22` | The SELECT projection and `_POINT_FIELDS` have the same fixed number of columns, and `_check_point_fields` rejects missing fields before iteration; disabling strict zip cannot change row mapping for this query. |
| `osm_polygon_lucas.point_coverage.x__taxonomy_digest__mutmut_20` | Python resolves `UTF-8` and `utf-8` to the same codec, so the digest bytes are unchanged. |
| `osm_polygon_lucas.point_coverage.x__prepare_polygons__mutmut_32` | An empty Shapely `STRtree` returns no candidates just like the `None` fast path; the empty-source test confirms valid GPS observations remain classified as outside. |
| `osm_polygon_lucas.point_coverage.x__validate_polygon_id__mutmut_7` | This changes only diagnostic wording; invalid IDs still raise `ValueError`, and exact wording is not a public contract. |
| `osm_polygon_lucas.point_coverage.x__validate_polygon_id__mutmut_8` | This changes only diagnostic capitalization; invalid IDs still raise `ValueError`, and exact wording is not a public contract. |
| `osm_polygon_lucas.point_coverage.x__record_quality_valid_observation__mutmut_8` | `typing.cast` is a runtime identity operation. Changing its type argument to `None` has no runtime effect. |
| `osm_polygon_lucas.point_coverage.x__record_quality_valid_observation__mutmut_13` | `typing.cast` is a runtime identity operation. Changing its type argument to `None` has no runtime effect. |
| `osm_polygon_lucas.point_coverage.x__remember_observation__mutmut_9` | This changes only the duplicate-ID diagnostic text; conflicting records still raise `ValueError`. |
| `osm_polygon_lucas.point_coverage.x__remember_observation__mutmut_10` | This changes only the capitalization of the duplicate-ID diagnostic; conflicting records still raise `ValueError`. |
| `osm_polygon_lucas.point_coverage.x__validate_observation_id__mutmut_5` | This changes only diagnostic wording; invalid observation IDs still raise `ValueError`. |
| `osm_polygon_lucas.point_coverage.x__validate_observation_id__mutmut_6` | This changes only diagnostic capitalization; invalid observation IDs still raise `ValueError`. |
| `osm_polygon_lucas.point_coverage.x__observation_mode__mutmut_9` | The mutated condition still calls the same mapping lookup; `dict.get(None)` returns `None`, as does the original conditional for an unknown mode. |
| `osm_polygon_lucas.point_coverage.x__decisions__mutmut_16` | Only the message for an impossible internal missing-anchor invariant changes; the same `ValueError` is raised. |
| `osm_polygon_lucas.point_coverage.x__decisions__mutmut_17` | Only the missing-anchor diagnostic text changes; the same internal `ValueError` is raised. |
| `osm_polygon_lucas.point_coverage.x__decisions__mutmut_18` | Only the capitalization of the missing-anchor diagnostic changes; the same internal `ValueError` is raised. |
| `tools.lucas_point_census_domain.x__candidate_link__mutmut_13` | Only the diagnostic truncation changes: it lists one extra unknown polygon ID; candidate rejection and returned results are unchanged. |
| `tools.lucas_point_census_domain.x__checkpoint_maps__mutmut_11` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x__checkpoint_maps__mutmut_19` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x__coordinate_pair__mutmut_17` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x__coordinate_pair__mutmut_4` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x__label_fields__mutmut_13` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x__merge_partition_row__mutmut_20` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x__merge_polygon_links__mutmut_13` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x__record_label__mutmut_4` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x__replayed_candidate_keys__mutmut_16` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x__validate_anchor_bounds__mutmut_16` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x__validate_label_join__mutmut_14` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x__validate_label_join__mutmut_3` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x__validate_label_join__mutmut_4` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x__validate_label_join__mutmut_9` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x__validate_polygon_label__mutmut_5` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x__validate_sentence_index__mutmut_7` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x__validated_anchor__mutmut_5` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x__validated_links__mutmut_12` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x_canonical_json__mutmut_2` | `ensure_ascii=None` and `ensure_ascii=False` select the same encoder in Python, so serialized bytes are unchanged. |
| `tools.lucas_point_census_domain.x_collect_candidate_links__mutmut_22` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x_collect_candidate_links__mutmut_23` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x_collect_candidate_links__mutmut_24` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x_invalid_geometry_digest__mutmut_11` | Python resolves these codec names case-insensitively; the resulting UTF-8/ASCII bytes and decoded text are unchanged. |
| `tools.lucas_point_census_domain.x_invalid_geometry_digest__mutmut_17` | Python resolves these codec names case-insensitively; the resulting UTF-8/ASCII bytes and decoded text are unchanged. |
| `tools.lucas_point_census_domain.x_invalid_geometry_digest__mutmut_7` | Python resolves these codec names case-insensitively; the resulting UTF-8/ASCII bytes and decoded text are unchanged. |
| `tools.lucas_point_census_domain.x_payload_sha256__mutmut_5` | Python resolves these codec names case-insensitively; the resulting UTF-8/ASCII bytes and decoded text are unchanged. |
| `tools.lucas_point_census_domain.x_validate_text_key_replay__mutmut_10` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x_validate_text_key_replay__mutmut_13` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x_validate_text_key_replay__mutmut_14` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_census_domain.x_validate_text_key_replay__mutmut_15` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__all_polygon_paths__mutmut_16` | The affected pinned geometry or label path template contains no `{stream}` placeholder; stream-specific source paths use separate text/label templates. The resolved path is unchanged for the configured pools. |
| `tools.lucas_point_source_census.x__all_polygon_paths__mutmut_18` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__all_polygon_paths__mutmut_9` | The affected pinned geometry or label path template contains no `{stream}` placeholder; stream-specific source paths use separate text/label templates. The resolved path is unchanged for the configured pools. |
| `tools.lucas_point_source_census.x__checkpoint_path__mutmut_16` | For valid run identities, at least one of `source_id`, `region`, or `stream` is present, so the fallback stem is unreachable; its spelling cannot change a valid checkpoint path. |
| `tools.lucas_point_source_census.x__checkpoint_path__mutmut_17` | For valid run identities, at least one of `source_id`, `region`, or `stream` is present, so the fallback stem is unreachable; its spelling cannot change a valid checkpoint path. |
| `tools.lucas_point_source_census.x__fingerprint_polygon_inputs__mutmut_18` | Both sides of this zip are constructed from the same input path/fingerprint sequence, so their lengths match by construction; changing or removing `strict=True` yields the same pairs. |
| `tools.lucas_point_source_census.x__fingerprint_polygon_inputs__mutmut_21` | Both sides of this zip are constructed from the same input path/fingerprint sequence, so their lengths match by construction; changing or removing `strict=True` yields the same pairs. |
| `tools.lucas_point_source_census.x__fingerprint_polygon_inputs__mutmut_22` | Both sides of this zip are constructed from the same input path/fingerprint sequence, so their lengths match by construction; changing or removing `strict=True` yields the same pairs. |
| `tools.lucas_point_source_census.x__fingerprint_polygon_inputs__mutmut_27` | Both sides of this zip are constructed from the same input path/fingerprint sequence, so their lengths match by construction; changing or removing `strict=True` yields the same pairs. |
| `tools.lucas_point_source_census.x__fingerprint_polygon_inputs__mutmut_30` | Both sides of this zip are constructed from the same input path/fingerprint sequence, so their lengths match by construction; changing or removing `strict=True` yields the same pairs. |
| `tools.lucas_point_source_census.x__fingerprint_polygon_inputs__mutmut_31` | Both sides of this zip are constructed from the same input path/fingerprint sequence, so their lengths match by construction; changing or removing `strict=True` yields the same pairs. |
| `tools.lucas_point_source_census.x__load_or_create_candidate_partition__mutmut_28` | Both sides of this zip are constructed from the same input path/fingerprint sequence, so their lengths match by construction; changing or removing `strict=True` yields the same pairs. |
| `tools.lucas_point_source_census.x__load_or_create_candidate_partition__mutmut_31` | Both sides of this zip are constructed from the same input path/fingerprint sequence, so their lengths match by construction; changing or removing `strict=True` yields the same pairs. |
| `tools.lucas_point_source_census.x__load_or_create_candidate_partition__mutmut_32` | Both sides of this zip are constructed from the same input path/fingerprint sequence, so their lengths match by construction; changing or removing `strict=True` yields the same pairs. |
| `tools.lucas_point_source_census.x__prepare_lucas_asset__mutmut_5` | Python resolves these codec names case-insensitively; the resulting UTF-8/ASCII bytes and decoded text are unchanged. |
| `tools.lucas_point_source_census.x__read_checkpoint__mutmut_6` | Python resolves these codec names case-insensitively; the resulting UTF-8/ASCII bytes and decoded text are unchanged. |
| `tools.lucas_point_source_census.x__read_checkpoint__mutmut_8` | This only removes the checkpoint path from validation diagnostics; schema, identity, result type, and payload-digest checks are unchanged. |
| `tools.lucas_point_source_census.x__read_polygon_region__mutmut_26` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__read_polygon_region__mutmut_27` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__read_polygon_region__mutmut_28` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__read_polygon_region__mutmut_29` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__record_polygon__mutmut_4` | Conflicting geometry still raises `ValueError`; the mutant only loses the polygon ID from the error message. |
| `tools.lucas_point_source_census.x__region_paths__mutmut_40` | The affected pinned geometry or label path template contains no `{stream}` placeholder; stream-specific source paths use separate text/label templates. The resolved path is unchanged for the configured pools. |
| `tools.lucas_point_source_census.x__region_paths__mutmut_59` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__region_paths__mutmut_8` | The affected pinned geometry or label path template contains no `{stream}` placeholder; stream-specific source paths use separate text/label templates. The resolved path is unchanged for the configured pools. |
| `tools.lucas_point_source_census.x__region_polygon_ids__mutmut_20` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__region_polygon_ids__mutmut_24` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__region_polygon_ids__mutmut_25` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__region_polygon_ids__mutmut_26` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__region_polygon_ids__mutmut_27` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__reject_mixed_snapshot_options__mutmut_4` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__reject_mixed_snapshot_options__mutmut_5` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__reject_mixed_snapshot_options__mutmut_6` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__require_single_snapshot_source__mutmut_7` | Only diagnostic wording or capitalization changes; acceptance, rejection, returned values, and exception type remain unchanged. |
| `tools.lucas_point_source_census.x__require_single_snapshot_source__mutmut_8` | Only diagnostic wording or capitalization changes; acceptance, rejection, returned values, and exception type remain unchanged. |
| `tools.lucas_point_source_census.x__require_single_snapshot_source__mutmut_9` | Only diagnostic wording or capitalization changes; acceptance, rejection, returned values, and exception type remain unchanged. |
| `tools.lucas_point_source_census.x__selected_regions__mutmut_20` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__selected_stream_names__mutmut_11` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__validate_all_source_arguments__mutmut_10` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__validate_all_source_arguments__mutmut_11` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__validate_all_source_arguments__mutmut_2` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__validate_all_source_arguments__mutmut_3` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__validate_all_source_arguments__mutmut_4` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__validate_all_source_arguments__mutmut_6` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__validate_all_source_arguments__mutmut_7` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__validate_all_source_arguments__mutmut_8` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__validate_all_source_arguments__mutmut_9` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__validate_cli_args__mutmut_1` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__validate_cli_paths__mutmut_19` | Only diagnostic wording or capitalization changes; acceptance, rejection, returned values, and exception type remain unchanged. |
| `tools.lucas_point_source_census.x__validate_cli_paths__mutmut_23` | Only diagnostic wording or capitalization changes; acceptance, rejection, returned values, and exception type remain unchanged. |
| `tools.lucas_point_source_census.x__validate_cli_paths__mutmut_24` | Only diagnostic wording or capitalization changes; acceptance, rejection, returned values, and exception type remain unchanged. |
| `tools.lucas_point_source_census.x__validate_seed__mutmut_4` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__validate_separate_path__mutmut_2` | `Path.resolve(strict=None)` follows the same non-strict resolution path as `strict=False`; containment checks and accepted paths are unchanged. |
| `tools.lucas_point_source_census.x__validate_separate_path__mutmut_5` | `Path.resolve(strict=None)` follows the same non-strict resolution path as `strict=False`; containment checks and accepted paths are unchanged. |
| `tools.lucas_point_source_census.x__validate_target_per_label__mutmut_7` | Only validation diagnostic wording or capitalization changes; the same rejection branch and exception/CLI exit behavior remain. |
| `tools.lucas_point_source_census.x__validated_checkpoint_result__mutmut_15` | This only removes the checkpoint path from validation diagnostics; schema, identity, result type, and payload-digest checks are unchanged. |
| `tools.lucas_point_source_census.x__validated_checkpoint_result__mutmut_2` | This only removes the checkpoint path from validation diagnostics; schema, identity, result type, and payload-digest checks are unchanged. |
| `tools.lucas_point_source_census.x__verify_candidate_geometry_signatures__mutmut_11` | The path list starts with exactly one geometry entry, followed only by text and label entries; after the first non-geometry role, `continue` and `break` behave identically. |
| `tools.lucas_point_source_census.x__verify_candidate_geometry_signatures__mutmut_16` | Only diagnostic wording or capitalization changes; acceptance, rejection, returned values, and exception type remain unchanged. |
| `tools.lucas_point_source_census.x__verify_candidate_geometry_signatures__mutmut_3` | Both sides of this zip are constructed from the same input path/fingerprint sequence, so their lengths match by construction; changing or removing `strict=True` yields the same pairs. |
| `tools.lucas_point_source_census.x__verify_candidate_geometry_signatures__mutmut_6` | Both sides of this zip are constructed from the same input path/fingerprint sequence, so their lengths match by construction; changing or removing `strict=True` yields the same pairs. |
| `tools.lucas_point_source_census.x__verify_candidate_geometry_signatures__mutmut_7` | Both sides of this zip are constructed from the same input path/fingerprint sequence, so their lengths match by construction; changing or removing `strict=True` yields the same pairs. |
| `tools.lucas_point_source_census.x__verify_file_signatures__mutmut_3` | Both sides of this zip are constructed from the same input path/fingerprint sequence, so their lengths match by construction; changing or removing `strict=True` yields the same pairs. |
| `tools.lucas_point_source_census.x__verify_file_signatures__mutmut_6` | Both sides of this zip are constructed from the same input path/fingerprint sequence, so their lengths match by construction; changing or removing `strict=True` yields the same pairs. |
| `tools.lucas_point_source_census.x__verify_file_signatures__mutmut_7` | Both sides of this zip are constructed from the same input path/fingerprint sequence, so their lengths match by construction; changing or removing `strict=True` yields the same pairs. |
| `tools.lucas_point_source_census.x__write_checkpoint__mutmut_20` | `ensure_ascii=None` and `ensure_ascii=False` select the same encoder in Python, so serialized bytes are unchanged. |
| `tools.lucas_point_source_census.x__write_checkpoint__mutmut_37` | Python resolves these codec names case-insensitively; the resulting UTF-8/ASCII bytes and decoded text are unchanged. |
| `tools.lucas_point_source_census.x__write_new_json__mutmut_14` | Python resolves these codec names case-insensitively; the resulting UTF-8/ASCII bytes and decoded text are unchanged. |
| `tools.lucas_point_source_census.x__write_new_json__mutmut_18` | `ensure_ascii=None` and `ensure_ascii=False` select the same encoder in Python, so serialized bytes are unchanged. |

## Historical preserved run status

The preserved mutation run for `src/osm_polygon_lucas/lucas_gpkg.py` generated 406 mutants: 368 were killed and 38 survived. Its raw status metadata and log are local-only scratch artifacts and are not included in this repository. The implementation code was unchanged when the diagnostic regression tests below were added.

An independent review classified 25 survivors as behavior-equivalent under the loader contract and 13 as diagnostic gaps. The 13 diagnostic gaps now have focused assertions in `tests/test_lucas_gpkg.py`. A separate focused run with the pinned Python 3.12/mutmut 3.8.0 environment executed those 13 mutants; all 13 were killed. It generated a catalog of 749 mutants but did not execute the other 736, so this is not a new overall mutation score or a rerun of the preserved run-7. The complete synthetic suite also passes in the pinned environment. See `docs/quality/mutation-environment.md` for setup, commands, and scope.

## Focused CLI and geometry mutation pass (2026-10-15)

A fresh disposable `/tmp` workspace mutated only `src/osm_polygon_lucas/cli.py` and `src/osm_polygon_lucas/polygon_coverage.py`. The selected tests were `tests/test_cli.py` and `tests/test_polygon_coverage.py`. Mutmut generated 516 mutants: 459 were killed and 57 survived; no mutant timed out or errored.

The survivors were reviewed by category. Strict-zip changes in support-evidence and class-grouping loops are equivalent because both paired sequences are built from the same supports. Geometry-validator survivors change diagnostic wording only; the area-predicate mutation is killed by a positive polygon smaller than one square unit. CLI survivors change help or diagnostic text, JSON formatting, UTF-8 spelling/defaults for the ASCII JSON payload, or an equivalent falsey `allow_nan` setting. Tests cover the behavior-sensitive cases: bounded RTree selection, CRS and required arguments, null dates, unmapped labels, sub-square-unit overlap evidence, and rejection of non-finite JSON values. The source-manifest test asserts the exact package and case-sensitive resource name independently of the host filesystem.

## Historical behavior-equivalent survivors

“Equivalent” here means no change to acceptance, rejection, or decoded values for inputs supported by the loader contract. Error class and feature/item identification remain intact; wording that describes the internal rejection stage is not part of that contract.

| Mutants | Reason |
| --- | --- |
| `_check_layer`: 12, 13, 38, 39, 41; `_check_crs`: 11, 12, 14, 15; `_check_rtree`: 10; `_select_rows`: 10, 13 | These change the case of SQL keywords or identifiers. SQLite treats the changed tokens case-insensitively; the SQL string values and parameters are unchanged. |
| `_wkb_start`: 24, 27, 30 | These vary `int.from_bytes(..., signed=...)`. EPSG:3035 is positive and decodes identically. Other signed or unsigned values still fail the SRS equality check; only the number in its diagnostic may differ. |
| `_wkb_start`: 38; `_check_signature`: 1, 2; `_wkb_order`: 1, 2 | These move a truncation check by one byte. Inputs at the changed boundary still fail as malformed header/envelope/WKB with `ValueError` and the feature id; only which validation stage reports it changes. |
| `_wkb_order`: 14 | For supported 2D WKB type codes 1–7, byte 1 is nonzero for little-endian and zero for big-endian, so testing it selects the same byte order as the marker at byte 0. Unsupported or malformed encodings remain rejected by type validation or Shapely decoding. |
| `_normalize_row`: 5, 8, 9 | These alter the strict zip behavior. `_select_rows` selects exactly `fid`, `geom`, and `REQUIRED_KEYS`; the unpacked `values` therefore always have the same length as `REQUIRED_KEYS`. |
| `_check_signature`: 9 | The magic-byte error displays one extra byte. Its predicate and all acceptance/rejection behavior are unchanged. |

## Historical diagnostic survivors now covered

| Mutants | Permanent assertion |
| --- | --- |
| `iter_lucas_2022_supports`: 31 | SQLite scan failures retain the source path and original cause. |
| `_parse_bounds`: 7; `_bound_values`: 5; `_finite_number`: 3, 6, 9 | Invalid bounds identify the failed condition, arity, number type, overflow, or non-finite value. |
| `_open_read_only`: 2, 11 | Missing-file and SQLite-open failures retain the requested path; open failures retain the cause. |
| `_check_layer`: 6, 32; `_check_crs`: 5 | Missing GeoPackage metadata rows identify the expected table/row. |
| `_check_signature`: 14 | Invalid version reports the version byte. |
| `_wkb_order`: 9 | Invalid byte order reports the byte-order marker. |

The corresponding tests are `test_invalid_bounds_keep_actionable_diagnostics`, `test_missing_file_error_includes_the_requested_path`, `test_sqlite_open_error_keeps_path_and_cause`, `test_sqlite_scan_error_keeps_path_and_cause`, `test_missing_metadata_rows_name_the_expected_item`, `test_bad_version_diagnostic_reports_the_version_byte`, and `test_bad_wkb_byte_order_diagnostic_reports_the_offending_byte`.

## Historical verification after triage tests

- Full synthetic suite: 199 passed on repeat runs, including a run with `PYTHONHASHSEED=20261015`.
- Line and branch coverage: 100% across the five implementation modules; the separate `__main__` wrapper is not included in that figure.
- Ruff lint, Ruff format check, and `ty` check: pass.
- Architecture and complexity pytest checks: pass as part of the full suite.
- Maximum cyclomatic complexity: 5. Maximum line and branch CRAP: 5.0.

All tests use synthetic GeoPackages in temporary directories. This report covers code-quality checks and is not a LUCAS/OSM benchmark result.
