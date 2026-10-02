# Homebrew formula template for HivePlane (M59-05).
# The release workflow publishes this to the `hiveplane` tap after PyPI upload,
# substituting `url` and `sha256` for the released sdist.
class Hiveplane < Formula
  include Language::Python::Virtualenv

  desc "Control plane for production agent fleets"
  homepage "https://github.com/deghosal-2026/hiveplane"
  url "https://files.pythonhosted.org/packages/source/h/hiveplane/hiveplane-0.2.0.tar.gz"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "Apache-2.0"
  head "https://github.com/deghosal-2026/hiveplane.git", branch: "main"

  depends_on "python@3.12"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "Usage", shell_output("#{bin}/hiveplane --help")
  end
end
