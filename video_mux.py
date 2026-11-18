"""Finalize processed videos with the FFmpeg libraries bundled by PyAV."""
from __future__ import annotations

from fractions import Fraction
from heapq import merge
from pathlib import Path
from typing import Iterator, Tuple


def _packet_timestamp(packet) -> float:
    timestamp = packet.dts if packet.dts is not None else packet.pts
    time_base = packet.time_base or packet.stream.time_base
    if timestamp is None or time_base is None:
        return 0.0
    return float(timestamp * time_base)


def finalize_video_with_audio(
    processed_video: Path,
    source_video: Path,
    final_video: Path,
    fallback_fps: float = 25.0,
) -> None:
    """Encode the click-overlay video as H.264 and copy any original audio."""
    try:
        import av
    except ImportError as exc:
        raise RuntimeError("The bundled video engine is unavailable. Reinstall Atlas.") from exc

    with (
        av.open(str(processed_video)) as processed,
        av.open(str(source_video)) as source,
        av.open(str(final_video), mode="w") as output,
    ):
        input_video = processed.streams.video[0]
        rate = input_video.average_rate
        if rate is None or float(rate) <= 0:
            rate = Fraction(str(fallback_fps if fallback_fps > 0 else 25.0)).limit_denominator(1000)
        else:
            rate = Fraction(int(rate.numerator), int(rate.denominator))

        video_stream = output.add_stream("libx264", rate=rate)
        video_stream.width = input_video.width
        video_stream.height = input_video.height
        video_stream.pix_fmt = "yuv420p"
        video_stream.options = {"preset": "ultrafast", "bf": "0"}

        input_audio = next(iter(source.streams.audio), None)
        audio_stream = output.add_stream(template=input_audio) if input_audio else None
        output.start_encoding()
        frame_time_base = Fraction(rate.denominator, rate.numerator)

        def encoded_video_packets() -> Iterator[Tuple[float, int, object]]:
            for frame_number, frame in enumerate(processed.decode(input_video)):
                frame.pts = frame_number
                frame.time_base = frame_time_base
                for packet in video_stream.encode(frame):
                    yield _packet_timestamp(packet), 0, packet
            for packet in video_stream.encode(None):
                yield _packet_timestamp(packet), 0, packet

        packet_streams = [encoded_video_packets()]
        if input_audio is not None and audio_stream is not None:
            def copied_audio_packets() -> Iterator[Tuple[float, int, object]]:
                for packet in source.demux(input_audio):
                    if packet.dts is None:
                        continue
                    timestamp = _packet_timestamp(packet)
                    packet.stream = audio_stream
                    yield timestamp, 1, packet

            packet_streams.append(copied_audio_packets())

        for _, _, packet in merge(*packet_streams):
            output.mux(packet)
