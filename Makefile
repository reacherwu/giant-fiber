.PHONY: all build test test-rust test-python bench demo clean

all: build

build:
	cargo build --release

test: test-rust test-python

test-rust:
	cargo test --workspace --verbose

test-python:
	python3 -m unittest discover -s tests -v

bench: build
	python3 benchmarks/latency_throughput.py

stress: build
	python3 benchmarks/stress_and_performance_suite.py

px4-test: build
	python3 -m unittest tests/test_px4_sitl_integration.py -v

px4-bench: build
	python3 benchmarks/px4_sitl_benchmark.py

sensor-bench: build
	python3 benchmarks/sensor_and_can_benchmark.py

assets:
	python3 scripts/generate_logo.py
	python3 scripts/generate_media_assets.py

demo: build
	python3 examples/nerf_incoming_demo.py

clean:
	cargo clean
