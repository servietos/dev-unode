# Goldcoin Core for Umbrel

Umbrel community-store app for a persistent Goldcoin (GLC) full node and a small browser-based JSON-RPC console.

## Services

- `node`: downloads the official Goldcoin Core v0.15.0 Linux release for amd64/arm64 on first start and stores chain data in `${APP_DATA_DIR}/data`.
- `web`: dashboard on port 3000 with blockchain/network/peer status and a JSON-RPC method console.
- `node-p2p`: publishes the node's P2P listener on host port 8334 (container port 8333).

The RPC endpoint is internal to the app's Docker network and is not published on a host port. RPC credentials are derived from Umbrel's `APP_SEED`. Treat the console as trusted-LAN administration: do not expose the app publicly. This initial app intentionally uses mainnet.

## Notes

The official GitHub latest release endpoint currently points to v0.15.0. The v0.17.0 release page describes beta builds, so this app pins the latest stable release instead of the beta. Official assets are fetched at runtime from `goldcoin/goldcoin`.

This app cannot be fully container-tested from the Hermes Umbrel sandbox because it has no reachable Docker daemon. Validate installation and startup on the Umbrel node before merging.
