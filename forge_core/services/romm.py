from forge_core.emulate import Emulator

class Romm:
    def __init__(self, version):
        self.version = version
        self.emulator = Emulator("Romm")

    def get_game_launch_info(self, game_id):
        """Return the EmulatorJS launch information for a game, or None if unsupported."""
        if self.version == "1.0.0":
            if game_id == "nes-super-mario-bros":
                return {
                    "core": "nes",
                    "rom": "super-mario-bros.nes",
                    "system": "NES"
                }
            elif game_id == "snes-super-mario-world":
                return {
                    "core": "snes",
                    "rom": "super-mario-world.smc",
                    "system": "SNES"
                }
        elif self.version == "1.1.0":
            if game_id == "nes-super-mario-bros":
                return {
                    "core": "nes",
                    "rom": "super-mario-bros.nes",
                    "system": "NES"
                }
        return None

    def validate_game_identifier(self, game_id):
        """Validate a game identifier and return the launch information if supported."""
        launch_info = self.get_game_launch_info(game_id)
        if launch_info is None:
            raise ValueError(f"Game {game_id} is not supported in RomM version {self.version}")
        return launch_info
