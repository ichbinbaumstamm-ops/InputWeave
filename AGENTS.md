# InputWeave repository instructions

- Before any release or push, run `./scripts/release-check.ps1` from the repository root and resolve every reported issue.
- Do not push or publish unless the user explicitly requests the push after reviewing the exact commit, GitHub repository, and target branch.
- Never print or copy secret values into chat or logs. If a real credential is found in the current files or Git history, stop and have the owner revoke or rotate it before considering history cleanup.
- Do not bypass Gitleaks findings with broad allowlists or by disabling the scan. Any finding or scanner error blocks release.
- The portable build includes third-party ViGEmBus and HidHide installers. Keep their official sources, versions, checksums, license notices, and Windows UAC flows aligned. On first launch, installers may auto-start only when the matching service is absent; never use silent installer flags or replace an existing driver.
- HidHide setup may add only the current InputWeave executable path to its application allow list. Preserve every other entry and never change the hidden-device list, inverse mode, or global cloak state automatically.
