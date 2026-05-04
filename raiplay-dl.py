# Created by WetCork
# Version 1.0.2 - September 2022
# https://github.com/wetcork/raiplay-dl

import argparse
import json
import math
import os
import pathlib
import shutil
import subprocess
import sys
import requests
from natsort import natsorted,ns

try:
    from rich.progress import Progress, DownloadColumn, BarColumn, TransferSpeedColumn, TimeRemainingColumn
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# GLOBAL SETTINGS #

debug = False # Print debug output in the console
url_root = 'https://www.raiplay.it'
override = '&overrideUserAgentRule=mp4-'
formats = ['5000', '3200', '2401', '2400', '1800', '1200', '0', '800', '700', '400', '250']
resolutions = ['1080p', '810p', '720p', '720p', '576p', '414p', '396p','288p', '288p', '288p', '198p']

# END GLOBAL SETTINGS #

def main(args):
    if check_url(args.url):
        data = get_json(args.url)
        if check_drm(data):
            serie = is_serie(data)
            if args.list_formats:
                if serie:
                    list_formats_serie(data, args.seasons, args.episodes)
                else:
                    list_formats(data)
            elif serie:
                pre_download_serie(data, args.seasons, args.episodes, args.format, args.out_dir)
            else:
                pre_download(data, args.format, args.out_dir)

def check_url(url): # Check if given url is valid
    if debug: print('[debug] Checking URL')
    
    if url_root in url:
        try:
            if requests.get(url).status_code == 404:
                sys.exit('[error] Can\'t connect to the url.')
            else:
                return True
        except:
            sys.exit('[error] Connection error')
    else:
        sys.exit('[error] Invalid url')

def check_drm(data): # Check if the content is DRM protected
    if debug: print('[debug] Checking DRM')
    
    if 'ContentItem' in data['id']:
        data = get_json(url_root + data['program_info']['path_id'])
        
    try:
        if data['program_info']['rights_management']['rights']['drm']['VOD']:
            print('[drm error] "%s" is DRM protected.' % (data['name']))
    except:
        return True
    else:
        sys.exit('[drm error] This script can\'t bypass DRM protection.')

def get_page_url(data): # Resolve a playable page URL for yt-dlp fallback
    page_url = data.get('weblink') or data.get('track_info', {}).get('page_url')
    if page_url:
        if page_url.startswith('http'):
            return page_url
        return url_root + page_url
    return None

def get_json(url): # Input the RaiPlay url and output the associated JSON
    if debug: print('[debug] Getting JSON')
    
    url = url.rstrip('/')
    if url.endswith('.html'):
        url = url.replace('.html', '.json')
    elif not url.endswith('.json'):
        url = url + '.json'

    if debug: print('[debug] ' + url)
    data = json.loads(requests.get(url).content)
    return data

def is_serie(data): # Check if the media is a tv serie or a movie
    if debug: print('[debug] Checking SERIE')
    
    layout = data['program_info']['layout']
    if layout == 'single':
        return False
    elif layout == 'multi':
        return True
    else:
        sys.exit('Error while defining serie.')

def get_override_url(data, format): # Generate the mp4 video url
    if debug:
        print('[debug] Getting OVERRIDE URL')
        print('[debug] ' + format)
    
    url = data['video']['content_url']
    if format == 'best':
        for format in formats:
            url_override = url + override + format
            
            if debug:
                    print('[debug] Format ' + format)
                    print('[debug] ' + url_override)
            try:    
                if requests.get(url_override, stream=True).headers['Content-Type'] == 'video/mp4':
                    return url_override
            except:
                sys.exit('[error] Connection error or the title has Verimatrix DRM protection.')
        return None
    else:
        url_override = url + override + format
        try:    
            if requests.get(url_override, stream=True).headers['Content-Type'] == 'video/mp4':
                return url_override
            else:
                #print('[info] Selected format is not avaiable, fallback to the best avaible format')
                for format in formats:
                    url_override = url + override + format
                    
                    if debug:
                            print('[debug] Format ' + format)
                            print('[debug] ' + url_override)
                            
                    if requests.get(url_override, stream=True).headers['Content-Type'] == 'video/mp4':
                        return url_override 
        except:
            return None
        return None

def get_yt_dlp_info(page_url): # Extract metadata with yt-dlp for fallback downloads
    if page_url is None:
        return None

    try:
        from yt_dlp import YoutubeDL as YDL
    except ImportError:
        return None

    ydl_opts = {
        'quiet': True,
        'skip_download': True,
        'noplaylist': True,
    }
    with YDL(ydl_opts) as ydl:
        return ydl.extract_info(page_url, download=False)

def pick_yt_dlp_format(info, format): # Pick the closest yt-dlp format to the requested RaiPlay quality
    if not info:
        return None

    available = [f for f in info.get('formats', []) if f.get('url') and f.get('height')]
    if not available:
        return None

    if format == 'best':
        return max(available, key=lambda f: (f.get('height') or 0, f.get('tbr') or 0))

    definition = get_definition(format)
    if not definition:
        return max(available, key=lambda f: (f.get('height') or 0, f.get('tbr') or 0))

    target_height = int(definition.rstrip('p'))
    return min(available, key=lambda f: (abs((f.get('height') or 0) - target_height), f.get('height') or 0))

def get_download_source(data, format): # Resolve the best available download source
    direct_url = get_override_url(data, format)
    if direct_url:
        return {
            'backend': 'direct',
            'url': direct_url,
            'page_url': get_page_url(data),
            'definition': get_definition(direct_url[direct_url.find('-') + 1:]),
        }

    page_url = get_page_url(data)
    info = get_yt_dlp_info(page_url)
    selected_format = pick_yt_dlp_format(info or {}, format)
    if selected_format:
        return {
            'backend': 'yt-dlp',
            'page_url': page_url,
            'format_id': selected_format.get('format_id'),
            'definition': '%sp' % (selected_format.get('height')),
        }

    return None

def download_with_yt_dlp(page_url, file_path, format_id): # Download using yt-dlp fallback
    if page_url is None:
        return False

    try:
        from yt_dlp import YoutubeDL as YDL
    except ImportError:
        return False

    ydl_opts = {
        'noplaylist': True,
        'outtmpl': file_path,
        'merge_output_format': 'mp4',
        'keepvideo': False,
        'quiet': False,
        'progress_hooks': [],
    }
    if format_id:
        ydl_opts['format'] = format_id

    try:
        with YDL(ydl_opts) as ydl:
            ydl.download([page_url])
        for temp_file in [file_path + '.part', f"{file_path}.ytdl", file_path.replace('.mp4', '.m3u8')]:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass
        return verify_and_repair_video(file_path)
    except Exception:
        return False

def probe_video_stream(file_path): # Inspect the downloaded file if ffprobe is available
    if not shutil.which('ffprobe'):
        return None

    cmd = [
        'ffprobe',
        '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=codec_name,width,height,sample_aspect_ratio,display_aspect_ratio',
        '-of', 'json',
        file_path,
    ]

    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, text=True)
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        streams = data.get('streams', [])
        return streams[0] if streams else None
    except:
        return None

def repair_aspect_ratio(file_path, stream): # Try to fix broken SAR metadata with a lightweight re-encode
    if not stream or not shutil.which('ffmpeg'):
        return False

    codec = (stream.get('codec_name') or '').lower()
    if codec != 'h264':
        return False

    width = int(stream.get('width') or 0)
    height = int(stream.get('height') or 0)
    if width < 1280 and height < 720:
        return False

    sample_aspect_ratio = (stream.get('sample_aspect_ratio') or '').strip()
    if sample_aspect_ratio in ('', '1:1', 'N/A', '0:1', '1:0'):
        return False

    temp_file = file_path + '.fixed.mp4'
    cmd = [
        'ffmpeg',
        '-y',
        '-i', file_path,
        '-map', '0',
        '-vf', 'setsar=1',
        '-c:v', 'libopenh264',
        '-c:a', 'copy',
        '-c:s', 'copy',
        temp_file,
    ]

    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0 or not os.path.exists(temp_file):
            if os.path.exists(temp_file):
                os.remove(temp_file)
            return False

        repaired_stream = probe_video_stream(temp_file)
        repaired_sar = (repaired_stream.get('sample_aspect_ratio') or '').strip() if repaired_stream else ''
        if repaired_sar != '1:1':
            os.remove(temp_file)
            return False

        os.replace(temp_file, file_path)
        print('[info] Fixed broken aspect ratio metadata')
        return True
    except:
        if os.path.exists(temp_file):
            os.remove(temp_file)
        return False

def verify_and_repair_video(file_path, expected_size=None): # Validate the download and repair it when possible
    if not os.path.exists(file_path):
        return False

    if expected_size is not None:
        actual_size = os.path.getsize(file_path)
        if actual_size != expected_size:
            try:
                os.remove(file_path)
            except:
                pass
            return False

    ffprobe_available = shutil.which('ffprobe') is not None
    stream = probe_video_stream(file_path)
    if ffprobe_available and stream is None:
        try:
            os.remove(file_path)
        except:
            pass
        return False

    if stream is not None:
        repair_aspect_ratio(file_path, stream)

    return os.path.exists(file_path)


def get_definition(format): # Retrive the video quality
    if debug: print('[debug] Getting DEFINITION')
    
    for bit in range(len(formats)):
        if formats[bit] == format:
            if debug: print('[debug] ' + resolutions[bit])
            return resolutions[bit]

def convert_size(size_bytes): # Covert file size from bytes to the beast readable option
    if size_bytes == 0:
        return '0B'
    size_name = ('B', 'KB', 'MB', 'GB')
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return '%s %s' % (s, size_name[i])

def path_and_down(url, out_dir, file_name, page_url=None, format_id=None): # Check output file path and start the download

    if out_dir.startswith('./') or out_dir.startswith('.\\'):
        out_dir = out_dir[2:]
    out_dir = out_dir.replace(':', ' -').replace('<', ' ').replace('>', '<').replace('|', '').replace('*', '').replace('?', '').replace('"', '')
    file_name = file_name.replace(':', ' -').replace('<', ' ').replace('>', '<').replace('|', '').replace('*', '').replace('?', '').replace('"', '').replace('/', '_').replace('\\', '_')
    file_path = os.path.join(out_dir, file_name)
    
    if debug:
        print('[debug] Checking PATH')
        print('[debug] ' + out_dir)
        print('[debug] ' + file_name)
        print()
    
    
    if not os.path.isfile(file_path):
            if not os.path.isdir(out_dir):
                os.makedirs(out_dir)
            print('Downloading "%s"' % (file_name.strip('.mp4')))
            if url:
                if download(url, file_path):
                    print('✓ Video downloaded')
                    return
            if page_url and download_with_yt_dlp(page_url, file_path, format_id):
                print('✓ Video downloaded')
                return
            sys.exit('[error] No format has been found for the given title')
    else:
        print('%s has already been downloaded' % file_name)
        if debug: print()

def pre_download(data, format, out_dir): # Get all the infos to start the download
    if debug: print('[debug] Starting PRE-DOWNLOAD')
    
    if 'Page' in data['id']:
        data = get_json(url_root + data['first_item_path'])
        
    if debug: print('[debug] Defining METADATA')
    title = data['program_info']['name']
    year = data['program_info']['year']
    source = get_download_source(data, format)

    if source:
        file_name = '%s (%s) [%s].mp4' % (title.strip(), year, source['definition'])
        if source['backend'] == 'direct':
            path_and_down(source['url'], out_dir, file_name, page_url=source['page_url'])
        else:
            path_and_down(None, out_dir, file_name, page_url=source['page_url'], format_id=source['format_id'])
    else:
        sys.exit('[error] No format has been found for the given title')

def pre_download_serie(data, def_seasons, def_episodes, format, out_dir): # Get all the infos to start the download
    if debug: print('[debug] Starting PRE-DOWNLOAD SERIE\n')

    if 'ContentItem' in data['id']:
        if debug: print('[debug] Defining METADATA')
        
        serie = data['program_info']['name']
        season = data['season']
        episode = data['episode']
        episode_title = data['episode_title']
        year = data['track_info']['edit_year']
        source = get_download_source(data, format)

        if source:
            file_name = '%s - %sx%s - %s (%s) [%s].mp4' % (serie, season.zfill(2), episode.zfill(2), episode_title.strip(), year, source['definition'])
            if source['backend'] == 'direct':
                path_and_down(source['url'], out_dir, file_name, page_url=source['page_url'])
            else:
                path_and_down(None, out_dir, file_name, page_url=source['page_url'], format_id=source['format_id'])
        else:
            sys.exit('[error] No format has been found for the given title')
    elif 'Page' in data['id']:
        def_seasons = [x.strip() for x in def_seasons.split(',')]
        def_episodes = [x.strip() for x in def_episodes.split(',')]
        
        fn_serie = data['name']
        fn_year = data['program_info']['year']
        print('Downloading "%s (%s)"\n' % (fn_serie.strip(), fn_year))
        
        for block in range(len(data['blocks'])):
            blocks_name = data['blocks'][block]['name'].strip()
            if 'Episodi' == blocks_name or 'Puntate' == blocks_name or 'Lingua italiana' == blocks_name:
                        seasons = []

                        if debug: print('[debug] Sorting SEASONS')
                        # This function stores the season name and the rispective json path
                        # in one array, separated by a custom word, then natsort the season
                        # for a better output in the console (it was to difficult to organize things for RAI)
                        for season in range(len(data['blocks'][block]['sets'])):
                            seasons.append(data['blocks'][block]['sets'][season]['name'] + '_SEP_' + data['blocks'][block]['sets'][season]['path_id'])
                        seasons = natsorted(seasons, alg=ns.IGNORECASE)

                        if debug: print('[debug] Getting custom SEASONS')
                        if def_seasons[0] != 'all':
                            temp = []
                            for i in def_seasons:
                                temp.append(seasons[int(i)-1])
                            seasons = temp
                        
                        for sor_season in seasons:
                            season_data = get_json(url_root + sor_season.split('_SEP_')[1])
                            
                            if debug: print('[debug] Defining seasons METADATA\n')
                            fn_season = season_data['items'][0]['season']
                            sub_dir = '%s (%s)\\Season %s' % (fn_serie.strip(), fn_year, fn_season)
                            out_sub_dir = os.path.join(out_dir, sub_dir)
                            
                            print('[Season %s]' % (season_data['items'][0]['season']))
                            
                            episodes = []
                            for episode in range(len(season_data['items'])):
                                if not season_data['items'][episode]['episode'] == '':
                                    episodes.append(season_data['items'][episode]['episode'])
                                else:
                                    episodes.append(episode+1)
                            
                            if def_episodes[0] != 'all':
                                if debug:
                                    print('\n[debug] Getting SELECTED EPISODES')
                                    print('[debug] ' + str(def_episodes))
                                    
                                for def_episode in def_episodes:
                                    for episode in range(len(episodes)):
                                        if def_episode == episodes[episode]:
                                            
                                            if debug: print('[debug] Defining episode METADATA')
                                            fn_episode = season_data['items'][episode]['episode']
                                            fn_episode_title = season_data['items'][episode]['episode_title']
                                            episode_data = get_json(url_root + season_data['items'][episode]['weblink'])
                                            source = get_download_source(episode_data, format)

                                            if source:
                                                file_name = '%s - %sx%s - %s [%s].mp4' % (fn_serie, fn_season.zfill(2), fn_episode.zfill(2), fn_episode_title.strip(), source['definition'])
                                                if source['backend'] == 'direct':
                                                    path_and_down(source['url'], out_sub_dir, file_name, page_url=source['page_url'])
                                                else:
                                                    path_and_down(None, out_sub_dir, file_name, page_url=source['page_url'], format_id=source['format_id'])
                                            else:
                                                sys.exit('[error] No format has been found for the given title')
                            else:   
                                if debug:
                                    print('\n[debug] Getting ALL EPISODES')
                                    print('[debug] ' + str(episodes))
                                
                                for episode in range(len(episodes)):
                                    fn_episode = season_data['items'][episode]['episode']
                                    fn_episode_title = season_data['items'][episode]['episode_title']
                                    episode_data = get_json(url_root + season_data['items'][episode]['weblink'])
                                    source = get_download_source(episode_data, format)

                                    if source:
                                        file_name = '%s - %sx%s - %s [%s].mp4' % (fn_serie, fn_season.zfill(2), fn_episode.zfill(2), fn_episode_title.strip(), source['definition'])
                                        if source['backend'] == 'direct':
                                                path_and_down(source['url'], out_sub_dir, file_name, page_url=source['page_url'])
                                        else:
                                            path_and_down(None, out_sub_dir, file_name, page_url=source['page_url'], format_id=source['format_id'])
                                    else:
                                        sys.exit('[error] No format has been found for the given title')
                            print()

def list_formats(data): # List the formats
    try:
        if 'Page' in data['id']:
            data = get_json(url_root + data['first_item_path'])
            
        if debug: print('[debug] Listing FORMATS\n')

        title = data['program_info']['name']
        year = data['program_info']['year']
        url = data['video']['content_url']

        print('Formats avaiable for "%s (%s)"' % (title.strip(), year))

        found = False

        for format in range(len(formats)):
            url_override = url + override + formats[format]
            r = requests.get(url_override, stream=True)
            if r.headers['Content-Type'] == 'video/mp4':
                found = True
                print('%s - %s (%s)' % (formats[format], resolutions[format], convert_size(int(r.headers['Content-Length']))))

        if not found:
            info = get_yt_dlp_info(get_page_url(data))
            if info:
                for format_info in sorted([f for f in info.get('formats', []) if f.get('height')], key=lambda f: (f.get('height') or 0, f.get('tbr') or 0), reverse=True):
                    height = format_info.get('height')
                    size = format_info.get('filesize') or format_info.get('filesize_approx')
                    if size:
                        print('%s - %sp (%s)' % (format_info.get('format_id'), height, convert_size(int(size))))
                    else:
                        print('%s - %sp' % (format_info.get('format_id'), height))
    except KeyboardInterrupt:
        sys.exit('\n[info] Format listing interrupted')

def list_formats_serie(data, def_seasons, def_episodes): # List the formats
    if debug: print('[debug] Listing FORMATS SERIE\n')
    try:
        if 'ContentItem' in data['id']:
            serie = data['program_info']['name']
            season = data['season']
            episode = data['episode']
            episode_title = data['episode_title']
            year = data['track_info']['edit_year']
            url = data['video']['content_url']
            
            print('Formats avaiable for "%s - %sx%s - %s (%s)"' % (serie.strip(), season.zfill(2), episode.zfill(2), episode_title.strip(), year))
            
            found = False
            for format in range(len(formats)):
                override_url = url + override + formats[format]
                r = requests.get(override_url, stream=True)
                if r.headers['Content-Type'] == 'video/mp4':
                    found = True
                    print('%s - %s (%s)' % (formats[format], resolutions[format], convert_size(int(r.headers['Content-Length']))))
            if not found:
                info = get_yt_dlp_info(get_page_url(data))
                if info:
                    for format_info in sorted([f for f in info.get('formats', []) if f.get('height')], key=lambda f: (f.get('height') or 0, f.get('tbr') or 0), reverse=True):
                        height = format_info.get('height')
                        size = format_info.get('filesize') or format_info.get('filesize_approx')
                        if size:
                            print('%s - %sp (%s)' % (format_info.get('format_id'), height, convert_size(int(size))))
                        else:
                            print('%s - %sp' % (format_info.get('format_id'), height))
        elif 'Page' in data['id']:
            def_seasons = [x.strip() for x in def_seasons.split(',')]
            def_episodes = [x.strip() for x in def_episodes.split(',')]
            
            fn_serie = data['name']
            fn_year = data['program_info']['year']
            
            print('Formats avaiable for "%s (%s)"\n' % (fn_serie.strip(), fn_year))
            if debug: print('[debug] Getting SEASONS')
            for block in range(len(data['blocks'])):
                if 'Episodi' == data['blocks'][block]['name'] or 'Puntate' == data['blocks'][block]['name']:
                            seasons = []
                            if debug: print('[debug] Sorting SEASONS')
                            # This function stores the season name and the rispective json path
                            # in one array, separated by a custom word, then natsort the season
                            # for a better output in the console (it was to difficult to organize things for RAI)
                            for season in range(len(data['blocks'][block]['sets'])):
                                seasons.append(data['blocks'][block]['sets'][season]['name'] + '_SEP_' + data['blocks'][block]['sets'][season]['path_id'])
                            seasons = natsorted(seasons, alg=ns.IGNORECASE)

                            if def_seasons[0] != 'all':
                                if debug: print('[debug] Getting custom SEASONS')
                                temp = []
                                for i in def_seasons:
                                    temp.append(seasons[int(i)-1])
                                seasons = temp
                            
                            for sor_season in seasons:
                                season_data = get_json(url_root + sor_season.split('_SEP_')[1])
                                
                                if debug: print()
                                print('[Season %s]' % (season_data['items'][0]['season']))
                                
                                episodes = []
                                for episode in range(len(season_data['items'])):
                                    if not season_data['items'][episode]['episode'] == '':
                                        episodes.append(season_data['items'][episode]['episode'])
                                    else:
                                        episodes.append(episode+1)
                                
                                if def_episodes[0] != 'all':
                                    if debug:
                                        print('\n[debug] Getting SELECTED EPISODES')
                                        print('[debug] ' + str(def_episodes))
                                        print()
                                        
                                    for def_episode in def_episodes:
                                        for episode in range(len(episodes)):
                                            if def_episode == episodes[episode]:
                                                fn_episode = season_data['items'][episode]['episode']
                                                fn_episode_title = season_data['items'][episode]['episode_title']
                                                
                                                print('Ep %s - "%s"' % (fn_episode, fn_episode_title.strip()))
                                                episode_data = get_json(url_root + season_data['items'][episode]['weblink'])
                                                url = season_data['items'][episode]['video_url']
                                                found = False
                                                for format in range(len(formats)):
                                                    override_url = url + override + formats[format]
                                                    r = requests.get(override_url, stream=True)
                                                    if r.headers['Content-Type'] == 'video/mp4':
                                                        found = True
                                                        print('%s - %s (%s)' % (formats[format], resolutions[format], convert_size(int(r.headers['Content-Length']))))
                                                if not found:
                                                    info = get_yt_dlp_info(get_page_url(episode_data))
                                                    if info:
                                                        for format_info in sorted([f for f in info.get('formats', []) if f.get('height')], key=lambda f: (f.get('height') or 0, f.get('tbr') or 0), reverse=True):
                                                            height = format_info.get('height')
                                                            size = format_info.get('filesize') or format_info.get('filesize_approx')
                                                            if size:
                                                                print('%s - %sp (%s)' % (format_info.get('format_id'), height, convert_size(int(size))))
                                                            else:
                                                                print('%s - %sp' % (format_info.get('format_id'), height))
                                                print()
                                        
                                else:   
                                    if debug:
                                        print('\n[debug] Getting ALL EPISODES')
                                        print('[debug] ' + str(episodes))
                                        print()
                                    
                                    for episode in range(len(episodes)):
                                        fn_episode = season_data['items'][episode]['episode']
                                        fn_episode_title = season_data['items'][episode]['episode_title']
                                        
                                        print('Ep %s - "%s"' % (fn_episode, fn_episode_title.strip()))
                                        episode_data = get_json(url_root + season_data['items'][episode]['weblink'])
                                        url = season_data['items'][episode]['video_url']
                                        found = False
                                        for format in range(len(formats)):
                                            override_url = url + override + formats[format]
                                            r = requests.get(override_url, stream=True)
                                            if r.headers['Content-Type'] == 'video/mp4':
                                                found = True
                                                print('%s - %s (%s)' % (formats[format], resolutions[format], convert_size(int(r.headers['Content-Length']))))
                                        if not found:
                                            info = get_yt_dlp_info(get_page_url(episode_data))
                                            if info:
                                                for format_info in sorted([f for f in info.get('formats', []) if f.get('height')], key=lambda f: (f.get('height') or 0, f.get('tbr') or 0), reverse=True):
                                                    height = format_info.get('height')
                                                    size = format_info.get('filesize') or format_info.get('filesize_approx')
                                                    if size:
                                                        print('%s - %sp (%s)' % (format_info.get('format_id'), height, convert_size(int(size))))
                                                    else:
                                                        print('%s - %sp' % (format_info.get('format_id'), height))
                                        print()
    except KeyboardInterrupt:
        sys.exit('\n[info] Format listing interrupted')
        
def download(url, file_path): # yeah this pretty much download
    if debug: print('\n[debug] Starting DOWNLOAD')
    try:
        with open(file_path, 'wb') as f:
            r = requests.get(url, stream=True)
            total_length = r.headers.get('Content-Length')
            if debug and total_length is not None: print('[debug] ' + total_length + '\n')
            if total_length is None:
                f.write(r.content)
            else:
                total_length = int(total_length)
                if HAS_RICH:
                    with Progress(
                        BarColumn(),
                        DownloadColumn(),
                        TransferSpeedColumn(),
                        TimeRemainingColumn(),
                    ) as progress:
                        task = progress.add_task('[cyan]Downloading...', total=total_length)
                        for data in r.iter_content(chunk_size=4096):
                            f.write(data)
                            progress.update(task, advance=len(data))
                else:
                    dl = 0
                    for data in r.iter_content(chunk_size=4096):
                        dl += len(data)
                        f.write(data)
                        done = int(50 * dl / total_length)
                        percent = int(100 * dl / total_length)
                        sys.stdout.write('\r[%s%s] %s%% of %s' % ('#' * done, ' ' * (50 - done), percent, convert_size(int(total_length))))
                        sys.stdout.flush()
                    print()
        if os.path.exists(file_path):
            if total_length is None or os.path.getsize(file_path) == total_length:
                verify_and_repair_video(file_path, expected_size=total_length)
                return True
        try:
            os.remove(file_path)
        except:
            pass
        return False
    except KeyboardInterrupt:
        os.remove(file_path)
        sys.exit('\n\n[info] Download canceled')
    except:
        try:
            os.remove(file_path)
        except:
            pass
        return False

if __name__ == '__main__':
    parser = argparse.ArgumentParser(prog='raiplay-dl', description='Downloader for RaiPlay')
    parser.add_argument('url', metavar='URL', help='Content URL')
    parser.add_argument('-f', '--format', metavar='FORMAT', dest='format', default='best', help='Video format code')
    parser.add_argument('-F', '--list-formats', dest='list_formats', help='List all available formats', action='store_true')
    parser.add_argument('-s', '--season', metavar='SEASON', dest='seasons', default='all', help='Season')
    parser.add_argument('-e', '--episode', metavar='EPISODE', dest='episodes', default='all', help='Episode')
    parser.add_argument('-o', '--output', metavar='PATH', dest='out_dir', default=str(pathlib.Path(__file__).parent.resolve()), help='Set the output directory')
    args = parser.parse_args()
    main(args)
