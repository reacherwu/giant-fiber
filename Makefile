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

demo: build
	python3 examples/nerf_incoming_demo.py

clean:
	cargo clean
