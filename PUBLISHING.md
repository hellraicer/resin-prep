# Publishing this project

The local repository is prepared for a public GitHub repository named **resin-prep**. Repository creation and upload still require an authenticated GitHub account. No remote URL is assumed in these files.

## Before publication

- Review the README, experimental status and GPL-3.0-or-later license.
- Publish the clean project export, not development reports or local test profiles.
- Make `dist/resin-prep-0.3.0.zip` available so users do not confuse the source archive with the Blender extension ZIP.

## GitHub CLI route

After authenticating GitHub CLI, run from this repository. Configure the commit identity using your own name and an email address you intend to publish, such as the GitHub-provided noreply address from your account settings. Do not put credentials in commands or files.

```sh
git config user.name "Your chosen author name"
git config user.email "Your GitHub noreply email"
git add .
git commit -m "Initial Resin Prep 0.3.0 experimental release"
gh auth login
gh repo create resin-prep --public --source . --remote origin --push --description "Blender resin-print preparation and selectable automatic repair"
gh release create v0.3.0 dist/resin-prep-0.3.0.zip dist/SHA256SUMS.txt --prerelease --title "Resin Prep 0.3.0 (experimental)" --notes-file RELEASE_NOTES.md
```

Do not run repository creation again if it already succeeded. Continue against the existing repository instead. A public repository can be read and cloned by anyone; retain the experimental scope and report limitations in the release.

## Browser route

Create an empty public repository named `resin-prep`. Extract the clean public source ZIP and upload its contents with GitHub's **Add file → Upload files** flow. Do not upload the enclosing ZIP as the only repository file. Do not upload the original development workspace with ignored generated data.

Create a prerelease for `v0.3.0`, attach the Blender ZIP and checksum file from `dist/`, and use `RELEASE_NOTES.md` as the release description. Verify the file download and GitHub Actions results after publication.

## Prepared versus executed

Local tests and package installation have been run. The GitHub workflow is provided but has not run on GitHub yet. A local Git repository and source export are not evidence that a GitHub repository was created.
