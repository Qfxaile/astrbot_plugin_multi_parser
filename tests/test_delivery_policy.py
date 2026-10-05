from astrbot_multi_parser.services.delivery_policy import DeliveryPolicy


def test_forward_policy_uses_strict_thresholds_and_fallbacks():
    policy = DeliveryPolicy(
        {
            "forward_mode": "threshold",
            "forward_image_threshold": 2,
            "forward_text_threshold": 10,
        }
    )

    assert policy.should_forward(2, 10) is False
    assert policy.should_forward(3, 10) is True
    assert policy.should_forward(2, 11) is True
    assert DeliveryPolicy({"forward_mode": "invalid"}).forward_mode() == "threshold"


def test_video_action_and_link_filter_are_normalized():
    policy = DeliveryPolicy(
        {
            "video_over_limit_action": "GROUP_FILE",
            "filter_output_links": True,
            "filtered_link_text": "[打开原文]",
        }
    )

    assert policy.video_over_limit_action() == "group_file"
    assert policy.filter_links_enabled() is True
    assert policy.filtered_link_text("default") == "[打开原文]"
    assert (
        DeliveryPolicy({"video_over_limit_action": "invalid"}).video_over_limit_action()
        == "direct_link"
    )
