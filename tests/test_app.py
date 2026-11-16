from __future__ import annotations

import unittest
from pathlib import Path

from app import CompressionJob, build_ffmpeg_command, calculate_bitrate_kbps, compressed_filename, parse_output_format, parse_target, safe_download_filename, safe_name


class ValidationTests(unittest.TestCase):
    def test_parse_target_accepts_decimal_megabytes(self) -> None:
        self.assertEqual(parse_target("20.5"), 20.5)

    def test_parse_target_rejects_zero(self) -> None:
        with self.assertRaisesRegex(Exception, "between"):
            parse_target("0")

    def test_parse_target_rejects_non_numeric_input(self) -> None:
        with self.assertRaisesRegex(Exception, "valid target"):
            parse_target("large")

    def test_safe_name_removes_path_and_unsafe_characters(self) -> None:
        self.assertEqual(safe_name("folder/holiday clip 01.mp4"), "holiday-clip-01.mp4")

    def test_safe_name_rejects_unknown_extensions(self) -> None:
        with self.assertRaisesRegex(Exception, "Use an MP4"):
            safe_name("notes.txt")

    def test_compressed_filename_keeps_original_stem(self) -> None:
        self.assertEqual(compressed_filename("holiday clip.mp4"), "holiday clip-sqzd.mp4")

    def test_download_filename_preserves_readable_spaces(self) -> None:
        self.assertEqual(safe_download_filename("holiday clip-sqzd.mp4"), "holiday clip-sqzd.mp4")

    def test_compressed_filename_uses_selected_output_format(self) -> None:
        self.assertEqual(compressed_filename("holiday clip.mov", "webm"), "holiday clip-sqzd.webm")

    def test_output_format_is_allowlisted(self) -> None:
        self.assertEqual(parse_output_format("MOV"), "mov")
        with self.assertRaisesRegex(Exception, "Choose MP4"):
            parse_output_format("avi")


class CompressionMathTests(unittest.TestCase):
    def test_bitrate_is_positive_for_a_normal_job(self) -> None:
        self.assertGreater(calculate_bitrate_kbps(20, 60), 0)

    def test_short_videos_still_get_a_bitrate(self) -> None:
        self.assertGreater(calculate_bitrate_kbps(1, 0), 160)

    def test_ffmpeg_command_does_not_use_a_shell(self) -> None:
        job = CompressionJob("abc", Path("source"), Path("output"), 20, "clip.mp4")
        command = build_ffmpeg_command("ffmpeg", job, 60, 1800)
        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("-threads", command)
        self.assertNotIn("shell=True", command)


if __name__ == "__main__":
    unittest.main()
