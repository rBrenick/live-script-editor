from setuptools import setup, find_packages

setup(name='live-script-editor',
      version='0.1',
      description='Live Script Editor',
      url='http://github.com/rBrenick/live-script-editor',
      author='Richard Brenick',
      author_email='RichardBrenick@gmail.com',
      license='MIT',
      zip_safe=False,

      install_requires=[
          "PySide2",
          "Qt.py"
      ],

      packages=find_packages(),

      package_data={'': ['*.*']},
      include_package_data=True,

      entry_points={
          'console_scripts': ['livescripter=live_script_editor.live_script_editor_ui:main'],
      },

      )
