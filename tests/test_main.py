import main


class TestMain:
    def test_sanitize_wait_seconds_minimum(self):
        assert main._sanitize_wait_seconds(0) == 1
        assert main._sanitize_wait_seconds(-5) == 1

    def test_sanitize_wait_seconds_maximum(self):
        assert (
            main._sanitize_wait_seconds(main.MAX_WAIT_SECONDS + 1)
            == main.MAX_WAIT_SECONDS
        )

    def test_sanitize_wait_seconds_passthrough(self):
        assert main._sanitize_wait_seconds(3600) == 3600
