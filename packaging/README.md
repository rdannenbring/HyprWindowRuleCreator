# Packaging

`PKGBUILD` builds `hyprwrc-git` — a VCS package tracking `main`. There is no
tagged release yet, so `pkgver()` falls back to `0.1.0.r<commits>.g<hash>`,
which still orders correctly for upgrades. Once a tag exists, `git describe`
takes over automatically.

## Build and test locally

```bash
cd packaging
makepkg -f                       # clones from GitHub, builds, runs the tests
namcap hyprwrc-git-*.pkg.tar.zst # lint
sudo pacman -U hyprwrc-git-*.pkg.tar.zst
```

`check()` deliberately runs only the tests that need no compositor. The rest
talk to a running Hyprland over its control socket, which a build chroot does
not have.

## Publishing to the AUR

Needs an AUR account with an SSH key registered, then:

```bash
git clone ssh://aur@aur.archlinux.org/hyprwrc-git.git aur-hyprwrc
cd aur-hyprwrc
cp ../PKGBUILD ../.SRCINFO .
git add PKGBUILD .SRCINFO
git commit -m "Initial import"
git push
```

Only `PKGBUILD` and `.SRCINFO` belong in the AUR repository — not the source.

Regenerate `.SRCINFO` after **any** PKGBUILD change; the AUR rejects pushes
where the two disagree:

```bash
makepkg --printsrcinfo > .SRCINFO
```

## Notes for whoever maintains this

- `pyproject.toml` lists no runtime dependencies on purpose. PyGObject, GTK4
  and libadwaita are system packages with C libraries behind them; pip
  installing them beside the distro's own copies is how desktops break. The
  real dependencies are declared here instead.
- namcap warns that `hyprland` and `slurp` "may not be needed". It cannot see
  them: Hyprland is reached over a Unix socket and slurp is launched as a
  subprocess. Both are required.
- namcap also warns about `hyprwrc.cli.main` being an uninstalled module. That
  is the generated entry-point script referring to its own package; harmless.
