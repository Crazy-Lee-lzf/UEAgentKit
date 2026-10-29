"""Resource limits for the R7 read-only mount PoC."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MountLimits:
    max_manifest_bytes: int = 256 * 1024
    max_pack_bytes: int = 16 * 1024 * 1024
    max_records_per_pack: int = 5_000
    max_mounted_packs: int = 8
    max_total_records: int = 20_000
    max_body_chars: int = 32_768
    max_title_chars: int = 512
    max_subject_key_chars: int = 256
    max_scopes_per_record: int = 32
    max_scope_key_chars: int = 512
    max_details_bytes: int = 16 * 1024
    max_query_chars: int = 128
    max_result_limit: int = 50
    max_candidate_cap: int = 200
    default_timeout_ms: int = 250
    max_timeout_ms: int = 2_000
    max_output_chars: int = 65_536
    max_body_preview_chars: int = 4_000
    default_body_preview_chars: int = 600
    max_short_query_scan_records: int = 20_000

    def validated(self) -> MountLimits:
        checks = {
            "max_manifest_bytes": self.max_manifest_bytes,
            "max_pack_bytes": self.max_pack_bytes,
            "max_records_per_pack": self.max_records_per_pack,
            "max_mounted_packs": self.max_mounted_packs,
            "max_total_records": self.max_total_records,
            "max_body_chars": self.max_body_chars,
            "max_title_chars": self.max_title_chars,
            "max_subject_key_chars": self.max_subject_key_chars,
            "max_scopes_per_record": self.max_scopes_per_record,
            "max_scope_key_chars": self.max_scope_key_chars,
            "max_details_bytes": self.max_details_bytes,
            "max_query_chars": self.max_query_chars,
            "max_result_limit": self.max_result_limit,
            "max_candidate_cap": self.max_candidate_cap,
            "default_timeout_ms": self.default_timeout_ms,
            "max_timeout_ms": self.max_timeout_ms,
            "max_output_chars": self.max_output_chars,
            "max_body_preview_chars": self.max_body_preview_chars,
            "default_body_preview_chars": self.default_body_preview_chars,
            "max_short_query_scan_records": self.max_short_query_scan_records,
        }
        for name, value in checks.items():
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
        if self.default_timeout_ms > self.max_timeout_ms:
            raise ValueError("default_timeout_ms must not exceed max_timeout_ms.")
        if self.default_body_preview_chars > self.max_body_preview_chars:
            raise ValueError("default_body_preview_chars must not exceed max_body_preview_chars.")
        if self.max_candidate_cap < self.max_result_limit:
            raise ValueError("max_candidate_cap must be at least max_result_limit.")
        return self
