import unittest

import gpus


class GpuMatchingTests(unittest.TestCase):
    def test_every_builtin_model_matches_its_own_title(self):
        for model in gpus.GPU_MODELS:
            with self.subTest(model=model["name"]):
                title = f"华硕 GeForce {model['name']} 旗舰台式显卡"
                self.assertTrue(gpus.title_matches_model(title, model["name"]))

    def test_specific_variants_do_not_match_base_model(self):
        cases = [
            ("RTX 5070 Ti", "RTX 5070"),
            ("RTX 4080 Super", "RTX 4080"),
            ("RTX 4070 Ti Super", "RTX 4070 Ti"),
            ("RTX 4060 Ti", "RTX 4060"),
            ("RTX 3090 Ti", "RTX 3090"),
        ]
        for title_model, target in cases:
            with self.subTest(title_model=title_model, target=target):
                self.assertFalse(
                    gpus.title_matches_model(f"GeForce {title_model} 台式显卡", target)
                )

    def test_5090_variants_are_mutually_exclusive(self):
        cases = [
            ("华硕 RTX 5090 32G 显卡", "RTX 5090"),
            ("七彩虹 RTX5090D 32G 显卡", "RTX 5090 D"),
            ("RTX 5090 D V2 公版显卡", "RTX 5090 D V2"),
            ("RTX5090DV2 32G 显卡", "RTX 5090 D V2"),
            ("RTX5090 V2版 全新显卡", "RTX 5090 D V2"),
        ]
        variants = {"RTX 5090", "RTX 5090 D", "RTX 5090 D V2"}
        for title, expected in cases:
            with self.subTest(title=title):
                matched = {name for name in variants if gpus.title_matches_model(title, name)}
                self.assertEqual({expected}, matched)

    def test_bait_non_product_and_faulty_listings_are_rejected(self):
        for title in (
            "RTX5090 显卡定金，拍前私聊",
            "RTX5090 水冷头单出",
            "RTX5090 修过显存颗粒",
            "RTX5090 打价贴勿扰，不卖",
            "RTX5090 货在国外，现在发不了",
            "RTX5090 只出改装好的散热器",
        ):
            with self.subTest(title=title):
                self.assertTrue(gpus.listing_rejection_reason(title))
        self.assertEqual("", gpus.listing_rejection_reason("RTX5090 32G 自用显卡 无拆无修"))

    def test_custom_model_requires_a_real_title_match(self):
        self.assertTrue(
            gpus.title_matches_model("蓝宝石 RX 7900 XTX 24G", "RX 7900 XTX")
        )
        self.assertFalse(
            gpus.title_matches_model("华硕 RTX 4090 台式显卡", "RX 7900 XTX")
        )

    def test_desktop_8g_gddr6_is_not_treated_as_mobile(self):
        self.assertTrue(gpus.is_desktop_gpu("RTX 4060 Ti 8G GDDR6 台式显卡"))
        self.assertFalse(gpus.is_desktop_gpu("RTX 4060 Laptop Mobile 游戏本"))

    def test_rx_custom_model_is_not_classified_as_rtx_40_series(self):
        info = gpus.infer_model("RX 7900 XTX")
        self.assertEqual("RX 7000 系", info["series"])
        self.assertEqual(70, info["generation"])


if __name__ == "__main__":
    unittest.main()
