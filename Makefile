.PHONY: all build test test-rust test-python bench demo nostd clippy validate connectome sitl clean

all: build

build:
	cargo build --release

test: test-rust test-python

test-rust:
	cargo test --workspace

test-python: build
	python3 -m pytest tests -q

clippy:
	cargo clippy --workspace --all-targets -- -D warnings

# no_std build for a Cortex-M4F target (rustup target add thumbv7em-none-eabihf)
nostd:
	cargo build -p giantfiber-core --no-default-features --features libm --target thumbv7em-none-eabihf --release

bench: build
	python3 benchmarks/run_benchmarks.py --json benchmarks/results.json

demo: build
	python3 examples/looming_demo.py

# Model validation against published looming-escape behaviour (writes docs/model/)
validate: build
	python3 tools/validation/validate_escape_model.py --out docs/model

# Re-extract the circuit from FlyWire v783 / MANC v1.2.1 (downloads ~1 GB on first run)
connectome:
	python3 tools/connectome/extract_escape_circuit.py --download

# Full PX4 SITL campaign (clones/builds PX4 v1.15.4 on first run; ~2 h)
sitl: build
	bash sim/px4_sitl/run_all.sh

clean:
	cargo clean
