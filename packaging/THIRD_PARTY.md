# Bundled Qt runtime

The binary package includes unmodified PySide6-Essentials 6.10.2 and shiboken6
6.10.2 Python package contents, including their dynamically linked Qt libraries.
It does not include PySide6-Addons or Qt WebEngine. Their original package
metadata remains under `/usr/lib/mx-creative-control/python/*.dist-info`.
The GPLv3 and LGPLv3 texts are provided in this application's documentation
`licenses/` directory; the upstream wheels do not include separate license files.

Qt for Python is offered under LGPLv3/GPLv3/commercial terms. See the upstream
licensing page for component terms and third-party notices. This project
does not change Qt or Shiboken. The private runtime is a normal directory; users
can replace its dynamically loaded libraries with compatible builds.

- Qt for Python package layout: https://doc.qt.io/qtforpython-6/package_details.html
- Qt for Python licensing: https://doc.qt.io/qtforpython-6/licenses.html
- Corresponding upstream Qt for Python 6.10.2 source archive:
  https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.10.2-src/
- Corresponding upstream Qt 6.10.2 source archive:
  https://download.qt.io/official_releases/qt/6.10/6.10.2/submodules/

Installed application icons are resolved locally from their desktop entries and
icon themes. Those application brands and icons are not bundled with this app.

The package also preserves license and copyright files from the Rust worker's
platform dependencies under `/usr/share/doc/mx-creative-control/licenses/rust/`,
with a version/license index. They are copied from Cargo's verified local cache;
the package build uses the lockfile and makes no dependency downloads.
