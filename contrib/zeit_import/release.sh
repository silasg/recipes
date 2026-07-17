#!/usr/bin/env bash
set -euo pipefail
# Publish a zeit-import release: create an annotated tag zeit-import/v<version>
# and push it. CI (.github/workflows/build-zeit-import-docker.yml) then builds
# ghcr.io/<owner>/recipes-zeit-import:{X.Y.Z, X.Y, X, latest}.
#
# Usage: ./release.sh <major|minor|patch|X.Y.Z>

cd "$(git rev-parse --show-toplevel)"

PREFIX="zeit-import/v"
git fetch --tags --quiet origin || echo "warn: could not fetch tags from origin - using local tags" >&2
LATEST=$(git tag -l "${PREFIX}*" --sort=-v:refname | head -1)
CURRENT="${LATEST#"$PREFIX"}"
CURRENT="${CURRENT:-0.0.0}"

case "${1:-}" in
    major|minor|patch)
        IFS=. read -r MA MI PA <<<"$CURRENT"
        case "$1" in
            major) MA=$((MA + 1)); MI=0; PA=0 ;;
            minor) MI=$((MI + 1)); PA=0 ;;
            patch) PA=$((PA + 1)) ;;
        esac
        VERSION="$MA.$MI.$PA"
        ;;
    [0-9]*.[0-9]*.[0-9]*)
        VERSION="$1"
        ;;
    *)
        echo "usage: $0 <major|minor|patch|X.Y.Z>   (latest release: ${LATEST:-none})" >&2
        exit 1
        ;;
esac

TAG="${PREFIX}${VERSION}"

# only the paths that end up in the image / drive the build must match HEAD
DIRTY=$(git status --porcelain -- contrib/zeit_import .github/workflows/build-zeit-import-docker.yml)
[ -z "${DIRTY}" ] || { echo "abort: uncommitted changes in release-relevant paths:" >&2; echo "${DIRTY}" >&2; exit 1; }
git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null && { echo "abort: ${TAG} already exists" >&2; exit 1; }
[ "$(git rev-parse HEAD)" = "$(git rev-parse '@{upstream}')" ] || { echo "abort: HEAD not pushed to upstream yet" >&2; exit 1; }
(cd contrib/zeit_import && python3 -m pytest tests/ -q) || { echo "abort: tests failed" >&2; exit 1; }

echo "latest release: ${LATEST:-none}"
echo "new release:    ${TAG}  ->  $(git log -1 --oneline)"
read -rp "tag and push? [y/N] " ok
[[ "${ok}" == y* ]] || { echo "aborted"; exit 1; }

git tag -a "${TAG}" -m "zeit-import ${VERSION}"
git push origin "${TAG}"
echo "pushed ${TAG} - CI publishes ghcr.io/<owner>/recipes-zeit-import:{${VERSION}, latest, ...}"
