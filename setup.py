import setuptools
import pywebchannel

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="pywebchannel",
    version=pywebchannel.__version__,
    author=pywebchannel.__author__,
    author_email=pywebchannel.__email__,
    description="An implementation of Qt's WebChannel protocol",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/menlosystems/pywebchannel",
    packages=setuptools.find_packages(),
    classifiers=(
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)",
        "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)",
        "Operating System :: OS Independent",
    ),
)
