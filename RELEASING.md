# Releasing Dooya RF

## First publication

1. Publish this directory as the public GitHub repository
   `twasilczyk/harf-dooya`. Its root must contain `hacs.json` and
   `custom_components/harf_dooya/`.
2. Enable issues, set a description such as "Dooya RF blinds for Home Assistant",
   and add topics such as `home-assistant`, `hacs`, `dooya`, and `radio-frequency`.
3. Check that the **Validate** workflow passes all jobs. HACS validation also
   checks the repository's GitHub metadata, so it needs the published repository.
4. Follow the release steps below for version `0.1.0`.

If you choose a different repository name or owner, update the URLs in
`README.md`, `custom_components/harf_dooya/manifest.json`, and this file first.

## Each release

1. Set `version` in `custom_components/harf_dooya/manifest.json`, for example
   `0.1.0`. Update the minimum `homeassistant` version in `hacs.json` when needed,
   and keep the README and tested Home Assistant version in sync.
2. Commit and push the changes, then wait for **Validate** to pass.
3. Tag that commit with the matching `v`-prefixed version, such as `v0.1.0`, and
   push the tag. Wait for its validation run to pass as well.
4. Publish a GitHub release for that tag, with release notes describing changes
   and any migration steps. HACS uses GitHub releases for versioned updates;
   creating only a tag does not publish a release.
5. Check installation through the README's HACS instructions on Home Assistant
   with a compatible RF transmitter.

HACS downloads `custom_components/harf_dooya/` from the release's source tree.
No wheel, custom ZIP artifact, or build step is needed for this layout.

## HACS catalog

Users can install this repository through **Custom repositories** as soon as it
is public. To make it searchable in the default catalog, follow the
[HACS inclusion instructions](https://www.hacs.xyz/docs/publish/include/) after
HACS validation and Hassfest pass and a GitHub release has been published.
The local `brand/icon.png` provides the integration's required brand asset.
