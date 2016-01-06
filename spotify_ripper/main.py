#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import unicode_literals

from colorama import init, Fore, AnsiToWin32
from spotify_ripper.ripper import Ripper
from spotify_ripper.utils import *
import os
import sys
import codecs
import argparse
import pkg_resources
import schedule
import signal

if sys.version_info >= (3, 0):
    import configparser as ConfigParser
else:
    import ConfigParser


def load_config(defaults):
    _settings_dir = settings_dir()
    config_file = os.path.join(_settings_dir, "config.ini")
    if os.path.exists(config_file):
        try:
            config = ConfigParser.SafeConfigParser()
            config.read(config_file)
            if not config.has_section("main"):
                return defaults
            config_items = dict(config.items("main"))

            to_array_options = [
                "directory", "key", "user", "password", "log",
                "genres", "format"]

            # coerce boolean and none types
            for _key in config_items:
                item = config_items[_key]
                if item == 'True':
                    config_items[_key] = True
                elif item == 'False':
                    config_items[_key] = False
                elif item == 'None':
                    config_items[_key] = None
                else:
                    config_items[_key] = item.strip("'\"")

                # certain options need to be in array (nargs=1)
                if _key in to_array_options:
                    item = config_items[_key]
                    if item is not None:
                        config_items[_key] = [item]

            # overwrite any existing defaults
            defaults.update(config_items)
        except ConfigParser.Error as e:
            print("\nError parsing config file: " + config_file)
            print(str(e))

    return defaults


def patch_bug_in_mutagen():
    from mutagen.mp4 import MP4Tags, MP4Cover
    from mutagen.mp4._atom import Atoms, Atom, AtomError
    import struct

    def _key2name(key):
        if sys.version_info >= (3, 0):
            return key.encode("latin-1")
        else:
            return key

    def __fixed_render_cover(self, key, value):
        atom_data = []
        for cover in value:
            try:
                imageformat = cover.imageformat
            except AttributeError:
                imageformat = MP4Cover.FORMAT_JPEG
            atom_data.append(Atom.render(
                b"data", struct.pack(">2I", imageformat, 0) + bytes(cover)))
        return Atom.render(_key2name(key), b"".join(atom_data))

    print(
        Fore.RED + "Monkey-patching MP4/Python 3.x bug in Mutagen" +
        Fore.RESET)
    MP4Tags.__fixed_render_cover = __fixed_render_cover
    MP4Tags._MP4Tags__atoms[b"covr"] = (
        MP4Tags._MP4Tags__parse_cover, MP4Tags.__fixed_render_cover)


def main(prog_args=sys.argv[1:]):
    # in case we changed the location of the settings directory where the
    # config file lives, we need to parse this argument before we parse
    # the rest of the arguments (which can overwrite the options in the
    # config file)
    settings_parser = argparse.ArgumentParser(add_help=False)
    settings_parser.add_argument(
        '-S', '--settings', nargs=1,
        help='Path to settings, config and temp files directory '
             '[Default=~/.spotify-ripper]')
    args, remaining_argv = settings_parser.parse_known_args(prog_args)
    init_util_globals(args)

    # load config file, overwriting any defaults
    defaults = {
        "bitrate": "320",
        "quality": "320",
        "comp": "10",
        "vbr": "0",
    }
    defaults = load_config(defaults)

    parser = argparse.ArgumentParser(
        prog='spotify-ripper',
        description='Rips Spotify URIs to MP3s with ID3 tags and album covers',
        parents=[settings_parser],
        formatter_class=argparse.RawTextHelpFormatter,
        epilog='''Example usage:
    rip a single file: spotify-ripper -u user -p password spotify:track:52xaypL0Kjzk0ngwv3oBPR
    rip entire playlist: spotify-ripper -u user -p password spotify:user:username:playlist:4vkGNcsS8lRXj4q945NIA4
    rip a list of URIs: spotify-ripper -u user -p password list_of_uris.txt
    search for tracks to rip: spotify-ripper -l -Q 160 -o "album:Rumours track:'the chain'"
    ''')

    # create group to prevent user from using both the -l and -u option
    is_user_set = defaults.get('user') is not None
    is_last_set = defaults.get('last') is True
    if is_user_set or is_last_set:
        if is_user_set and is_last_set:
            print("spotify-ripper: error: one of the arguments -u/--user "
                  "-l/--last is required")
            sys.exit(1)
        else:
            group = parser.add_mutually_exclusive_group(required=False)
    else:
        group = parser.add_mutually_exclusive_group(required=True)

    encoding_group = parser.add_mutually_exclusive_group(required=False)

    # set defaults
    parser.set_defaults(**defaults)

    prog_version = pkg_resources.require("spotify-ripper")[0].version
    parser.add_argument(
        '-a', '--ascii', action='store_true',
        help='Convert the file name and the metadata tags to ASCII '
             'encoding [Default=utf-8]')
    encoding_group.add_argument(
        '--aac', action='store_true',
        help='Rip songs to AAC format with FreeAAC instead of MP3')
    encoding_group.add_argument(
        '--alac', action='store_true',
        help='Rip songs to Apple Lossless format instead of MP3')
    parser.add_argument(
        '-A', '--ascii-path-only', action='store_true',
        help='Convert the file name (but not the metadata tags) to ASCII '
             'encoding [Default=utf-8]')
    parser.add_argument(
        '-b', '--bitrate', help='CBR bitrate [Default=320]')
    parser.add_argument(
        '-c', '--cbr', action='store_true', help='CBR encoding [Default=VBR]')
    parser.add_argument(
        '--comp', default="10",
        help='compression complexity for FLAC and Opus [Default=Max]')
    parser.add_argument(
        '--comment', nargs=1,
        help='Add custom metadata comment to all songs')
    parser.add_argument(
        '--cover-file', nargs=1,
        help='Save album cover image to file name (e.g "cover.jpg") '
             '[Default=embed]')
    parser.add_argument(
        '-d', '--directory', nargs=1,
        help='Base directory where ripped MP3s are saved [Default=cwd]')
    parser.add_argument(
        '--fail-log', nargs=1,
        help="Logs the list of track URIs that failed to rip"
    )
    encoding_group.add_argument(
        '--flac', action='store_true',
        help='Rip songs to lossless FLAC encoding instead of MP3')
    parser.add_argument(
        '-f', '--format', nargs=1,
        help='Save songs using this path and filename structure (see README)')
    parser.add_argument(
        '--flat', action='store_true',
        help='Save all songs to a single directory '
             '(overrides --format option)')
    parser.add_argument(
        '--flat-with-index', action='store_true',
        help='Similar to --flat [-f] but includes the playlist index at '
             'the start of the song file')
    parser.add_argument(
        '-g', '--genres', nargs=1,
        choices=['artist', 'album'],
        help='Attempt to retrieve genre information from Spotify\'s '
             'Web API [Default=skip]')
    encoding_group.add_argument(
        '--id3-v23', action='store_true',
        help='Store ID3 tags using version v2.3 [Default=v2.4]')
    parser.add_argument(
        '-k', '--key', nargs=1,
        help='Path to Spotify application key file '
             '[Default=Settings Directory]')
    group.add_argument(
        '-u', '--user', nargs=1,
        help='Spotify username')
    parser.add_argument(
        '-p', '--password', nargs=1,
        help='Spotify password [Default=ask interactively]')
    group.add_argument(
        '-l', '--last', action='store_true',
        help='Use last login credentials')
    parser.add_argument(
        '-L', '--log', nargs=1,
        help='Log in a log-friendly format to a file (use - to log to stdout)')
    encoding_group.add_argument(
        '--pcm', action='store_true',
        help='Saves a .pcm file with the raw PCM data instead of MP3')
    encoding_group.add_argument(
        '--mp4', action='store_true',
        help='Rip songs to MP4/M4A format with Fraunhofer FDK AAC codec '
             'instead of MP3')
    parser.add_argument(
        '--normalize', action='store_true',
        help='Normalize volume levels of tracks')
    parser.add_argument(
        '-o', '--overwrite', action='store_true',
        help='Overwrite existing MP3 files [Default=skip]')
    encoding_group.add_argument(
        '--opus', action='store_true',
        help='Rip songs to Opus encoding instead of MP3')
    parser.add_argument(
        '--playlist-m3u', action='store_true',
        help='create a m3u file when ripping a playlist')
    parser.add_argument(
        '--playlist-wpl', action='store_true',
        help='create a wpl file when ripping a playlist')
    parser.add_argument(
        '--playlist-sync', action='store_true',
        help='Sync playlist songs (rename and remove old songs)')
    parser.add_argument(
        '-q', '--vbr',
        help='VBR quality setting or target bitrate for Opus [Default=0]')
    parser.add_argument(
        '-Q', '--quality', choices=['160', '320', '96'],
        help='Spotify stream bitrate preference [Default=320]')
    parser.add_argument(
        '-s', '--strip-colors', action='store_true',
        help='Strip coloring from output [Default=colors]')
    parser.add_argument(
        '--stereo-mode', choices=['j', 's', 'f', 'd', 'm', 'l', 'r'],
        help='Advanced stereo settings for Lame MP3 encoder only')
    parser.add_argument(
        '-V', '--version', action='version', version=prog_version)
    encoding_group.add_argument(
        '--wav', action='store_true',
        help='Rip songs to uncompressed WAV file instead of MP3')
    encoding_group.add_argument(
        '--vorbis', action='store_true',
        help='Rip songs to Ogg Vorbis encoding instead of MP3')
    parser.add_argument(
        '-r', '--remove-from-playlist', action='store_true',
        help='Delete tracks from playlist after successful '
             'ripping [Default=no]')
    parser.add_argument(
        '-x', '--exclude-appears-on', action='store_true',
        help='Exclude albums that an artist \'appears on\' when passing '
             'a Spotify artist URI')
    parser.add_argument(
        'uri', nargs="+",
        help='One or more Spotify URI(s) (either URI, a file of URIs or a '
             'search query)')
    args = parser.parse_args(remaining_argv)
    init_util_globals(args)

    # kind of a hack to get colorama stripping to work when outputting
    # to a file instead of stdout.  Taken from initialise.py in colorama
    def wrap_stream(stream, convert, strip, autoreset, wrap):
        if wrap:
            wrapper = AnsiToWin32(stream, convert=convert,
                                  strip=strip, autoreset=autoreset)
            if wrapper.should_wrap():
                stream = wrapper.stream
        return stream

    args.has_log = args.log is not None
    if args.has_log:
        if args.log[0] == "-":
            init(strip=True)
        else:
            log_file = open(args.log[0], 'a')
            sys.stdout = wrap_stream(log_file, None, True, False, True)
    else:
        init(strip=True if args.strip_colors else None)

    if args.ascii_path_only is True:
        args.ascii = True

    # unless explicitly told not to, we are going to encode
    # for utf-8 by default
    if not args.ascii and sys.version_info < (3, 0):
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout)

    if args.wav:
        args.output_type = "wav"
    elif args.pcm:
        args.output_type = "pcm"
    elif args.flac:
        args.output_type = "flac"
        if args.comp == "10":
            args.comp = "8"
    elif args.vorbis:
        args.output_type = "ogg"
        if args.vbr == "0":
            args.vbr = "10"
    elif args.opus:
        args.output_type = "opus"
        if args.vbr == "0":
            args.vbr = "320"
    elif args.aac:
        args.output_type = "aac"
        if args.vbr == "0":
            args.vbr = "500"
    elif args.mp4:
        args.output_type = "m4a"
        if args.vbr == "0":
            args.vbr = "5"
    elif args.alac:
        args.output_type = "alac.m4a"
    else:
        args.output_type = "mp3"

    # check that encoder tool is available
    encoders = {
        "flac": ("flac", "flac"),
        "aac": ("faac", "faac"),
        "ogg": ("oggenc", "vorbis-tools"),
        "opus": ("opusenc", "opus-tools"),
        "mp3": ("lame", "lame"),
        "m4a": ("fdkaac", "fdk-aac-encoder"),
        "alac.m4a": ("avconv", "libav-tools"),
    }
    if args.output_type in encoders.keys():
        encoder = encoders[args.output_type][0]
        if which(encoder) is None:
            print(Fore.RED + "Missing dependency '" + encoder +
                  "'.  Please install and add to path..." + Fore.RESET)
            # assumes OS X or Ubuntu/Debian
            command_help = ("brew install " if sys.platform == "darwin"
                            else "sudo apt-get install ")
            print("...try " + Fore.YELLOW + command_help +
                  encoders[args.output_type][1] + Fore.RESET)
            sys.exit(1)

    # format string
    if args.flat:
        args.format = ["{artist} - {track_name}.{ext}"]
    elif args.flat_with_index:
        args.format = ["{idx:3} - {artist} - {track_name}.{ext}"]
    elif args.format is None:
        args.format = ["{album_artist}/{album}/{artist} - {track_name}.{ext}"]

    # print some settings
    print(Fore.GREEN + "Spotify Ripper - v" + prog_version + Fore.RESET)

    def encoding_output_str():
        if args.output_type == "wav":
            return "WAV, Stereo 16bit 44100Hz"
        elif args.output_type == "pcm":
            return "Raw Headerless PCM, Stereo 16bit 44100Hz"
        else:
            if args.output_type == "flac":
                return "FLAC, Compression Level: " + args.comp
            elif args.output_type == "alac.m4a":
                return "Apple Lossless (ALAC)"
            elif args.output_type == "ogg":
                codec = "Ogg Vorbis"
            elif args.output_type == "opus":
                codec = "Opus"
            elif args.output_type == "mp3":
                codec = "MP3"
            elif args.output_type == "m4a":
                codec = "MPEG4 AAC"
            elif args.output_type == "aac":
                codec = "AAC"
            else:
                codec = "Unknown"

            if args.cbr:
                return codec + ", CBR " + args.bitrate + " kbps"
            else:
                return codec + ", VBR " + args.vbr

    print(Fore.YELLOW + "  Encoding output:\t" +
          Fore.RESET + encoding_output_str())
    print(Fore.YELLOW + "  Spotify bitrate:\t" +
          Fore.RESET + args.quality + " kbps")

    def unicode_support_str():
        if args.ascii_path_only:
            return "Unicode tags, ASCII file path"
        elif args.ascii:
            return "ASCII only"
        else:
            return "Yes"

    print(Fore.YELLOW + "  Unicode support:\t" +
          Fore.RESET + unicode_support_str())
    print(Fore.YELLOW + "  Output directory:\t" + Fore.RESET +
          base_dir())
    print(Fore.YELLOW + "  Settings directory:\t" + Fore.RESET +
          settings_dir())

    print(Fore.YELLOW + "  Format String:\t" + Fore.RESET + args.format[0])
    print(Fore.YELLOW + "  Overwrite files:\t" +
          Fore.RESET + ("Yes" if args.overwrite else "No"))

    # patch a bug when Python 3/MP4
    if sys.version_info >= (3, 0) and args.output_type == "m4a":
        patch_bug_in_mutagen()

    ripper = Ripper(args)
    ripper.start()

    # try to listen for terminal resize events
    # (needs to be called on main thread)
    if not args.has_log:
        ripper.progress.handle_resize()
        signal.signal(signal.SIGWINCH, ripper.progress.handle_resize)

    def abort(set_logged_in=False):
        ripper.abort_rip()
        if set_logged_in:
            ripper.logged_in.set()
        ripper.join()
        sys.exit(1)

    # login on main thread to catch any KeyboardInterrupt
    try:
        if not ripper.login():
            print(
                Fore.RED + "Encountered issue while logging into "
                           "Spotify, aborting..." + Fore.RESET)
            abort(set_logged_in=True)

    except (KeyboardInterrupt, Exception) as e:
        if not isinstance(e, KeyboardInterrupt):
            print(str(e))
        print("\n" + Fore.RED + "Aborting..." + Fore.RESET)
        abort(set_logged_in=True)

    # wait for ripping thread to finish
    try:
        while ripper.isAlive():
            schedule.run_pending()
            ripper.join(0.1)
    except (KeyboardInterrupt, Exception) as e:
        if not isinstance(e, KeyboardInterrupt):
            print(str(e))
        print("\n" + Fore.RED + "Aborting..." + Fore.RESET)
        abort()

if __name__ == '__main__':
    main()
