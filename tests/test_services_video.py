from astrbot_multi_parser.services.video import VideoSendPolicy, VideoSizeInfo


def test_unknown_video_size_is_rejected_by_default():
    should_send, reason = VideoSendPolicy({}).decide(
        VideoSizeInfo(reason="服务端未返回视频大小")
    )

    assert should_send is False
    assert reason == "服务端未返回视频大小，未直接发送视频"


def test_video_size_policy_accepts_files_within_limit():
    should_send, reason = VideoSendPolicy({"max_video_size_mb": 1}).decide(
        VideoSizeInfo(size_bytes=512 * 1024)
    )

    assert should_send is True
    assert reason == "视频大小 0.50 MB，未超过限制"
