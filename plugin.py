"""Registration does not connect to the radio."""
def register(api):
    from pokeldn.plugins import Field, Tool
    api.add_tool('bdsp', Tool('bdsp-excavation', 'Automatic excavation', 'service.py',
        'Two pickaxe helpers clear each new wall automatically using one ESP32.',
        ('Enter the Grand Underground using Local Communication.',
         'Start this tool, then open a digging wall and close its initial message promptly.',
         'Keep the controls still until the sweep finishes. Enable automatic renewal to repeat on new walls.',
         'Press Stop to disconnect both helpers.'),
        fields=(Field('--renew', 'Automatic renewal', 'switch', default=False,
                      help='Enabled: repeat on new walls until Stop. Disabled: clear one wall and disconnect.'),),
        doc='README.md'))
