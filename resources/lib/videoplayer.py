# -*- coding: utf-8 -*-
# Crunchyroll
# Copyright (C) 2018 MrKrabat
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import threading
import time
from typing import Optional

import inputstreamhelper
import requests
import xbmc
import xbmcgui
import xbmcplugin

from . import utils, view
from .addons import upnext
from .api import API
from .gui import SkipModalDialog, _show_modal_dialog
from .model import Object, Args, CrunchyrollError, EpisodeData, SeriesData
from .videostream import VideoPlayerStreamData, VideoStream


class VideoPlayer(Object):
    """ Handles playing video using data contained in args object

    Keep instance of this class in scope, while playing, as threads started by it rely on it
    """

    def __init__(self, args: Args, api: API):
        self._args = args
        self._api = api

        self._stream_data: VideoPlayerStreamData | None = None
        # @todo: what about movies and other future content types?
        self._episode_data: EpisodeData | None = None
        self._series_data: SeriesData | None = None
        self._player: Optional[xbmc.Player] = xbmc.Player()  # @todo: what about garbage collection?

        self._skip_modal_duration_max = 10

    def start_playback(self):
        """ Set up player and start playback """

        if not self._get_video_stream_data():
            return

        # already playing for whatever reason?
        if self._player.isPlaying():
            utils.log("Skipping playback because already playing")

        self._load_playing_item_data()
        self._prepare_and_start_playback()

        self._handle_resume()
        self._handle_skipping()
        self._handle_upnext()

    def is_playing(self) -> bool:
        """ Returns true if playback is running. Note that it also returns true when paused. """

        if not self._stream_data:
            return False

        if not self._player.isPlaying():
            return False

        return self._stream_data.stream_url == self._player.getPlayingFile()

    def stop_playback(self):
        self._player.stop()

    def _get_video_stream_data(self) -> bool:
        """ Fetch all required stream data using VideoStream object """

        video_stream_helper = VideoStream(self._args, self._api)
        item = xbmcgui.ListItem(self._args.get_arg('title', 'Title not provided'))

        try:
            self._stream_data = video_stream_helper.get_player_stream_data()
            if not self._stream_data or not self._stream_data.stream_url:
                utils.crunchy_log(self._args, "Failed to load stream info for playback", xbmc.LOGERROR)
                xbmcplugin.setResolvedUrl(int(self._args.argv[1]), False, item)
                xbmcgui.Dialog().ok(self._args.addon_name, self._args.addon.getLocalizedString(30064))
                return False

        except (CrunchyrollError, requests.exceptions.RequestException):
            utils.log_error_with_trace(self._args, "Failed to prepare stream info data", False)
            xbmcplugin.setResolvedUrl(int(self._args.argv[1]), False, item)
            xbmcgui.Dialog().ok(self._args.addon_name,
                                self._args.addon.getLocalizedString(30064))
            return False

        return True

    def _prepare_and_start_playback(self):
        """ Sets up the playback"""

        # prepare playback
        item = self._prepare_xbmc_list_item()
        item.setPath(self._stream_data.stream_url)
        item.setMimeType("application/vnd.apple.mpegurl")
        item.setContentLookup(False)

        # inputstream adaptive
        is_helper = inputstreamhelper.Helper("hls")
        if is_helper.check_inputstream():
            item.setProperty("inputstream", "inputstream.adaptive")
            item.setProperty("inputstream.adaptive.manifest_type", "hls")

            # add soft subtitles url for configured language
            if self._stream_data.subtitle_urls:
                item.setSubtitles(self._stream_data.subtitle_urls)

            """ start playback"""
            xbmcplugin.setResolvedUrl(int(self._args.argv[1]), True, item)

            # wait for playback
            if wait_for_playback(10):
                # if successful wait more
                xbmc.sleep(3000)

        # start fallback
        if not wait_for_playback(2):
            # start without inputstream adaptive
            utils.crunchy_log(self._args, "Inputstream Adaptive failed, trying directly with kodi", xbmc.LOGINFO)
            item.setProperty("inputstream", "")
            self._player.play(self._stream_data.stream_url, item)

    def _load_playing_item_data(self):
        """ Load episode and series data from API """

        try:
            objects = utils.get_data_from_object_ids(self._args, [ self._args.get_arg("series_id"), self._args.get_arg("episode_id") ], self._api)
            self._episode_data = objects.get(self._args.get_arg("episode_id"))
            self._series_data = objects.get(self._args.get_arg("series_id"))
        except Exception:
            utils.crunchy_log(self._args, "Unable to find video metadata from episode %s" % self._args.get_arg("episode_id"), xbmc.LOGINFO)

    def _load_playing_item_data(self):
        """ Load episode and series data from API """

        try:
            objects = utils.get_data_from_object_ids(self._args, [self._args.get_arg("series_id"), self._args.get_arg("episode_id")],
                                                     self._api)
            self._episode_data = objects.get(self._args.get_arg("episode_id"))
            self._series_data = objects.get(self._args.get_arg("series_id"))
        except Exception:
            utils.crunchy_log(self._args, "Unable to find video metadata from episode %s" % self._args.get_arg("episode_id"),
                              xbmc.LOGINFO)

    def _prepare_xbmc_list_item(self):
        """ Create XBMC list item from API metadata """

        if not self._episode_data:
            utils.crunchy_log(self._args, "Unable to find video metadata from episode %s" % self._args.get_arg("episode_id"),
                              xbmc.LOGINFO)
            return xbmcgui.ListItem(getattr(self._args, "title", "Title not provided"))

        media_info = utils.create_media_info_from_objects_data(self._episode_data, self._series_data)
        seriesMetadata = utils.customFanart(self._series_data.series_id, self._series_data.tvshowtitle, self._series_data.year)
        if seriesMetadata:
            media_info.update({
                "fanart":        seriesMetadata["artworks"]["showbackground"]["camo_url"] if seriesMetadata and "artworks" in seriesMetadata and "showbackground" in seriesMetadata["artworks"] else media_info.get("fanart"),
                "clearlogo":     seriesMetadata["artworks"]["hdtvlogo"]["camo_url"] if seriesMetadata and "artworks" in seriesMetadata and "hdtvlogo" in seriesMetadata["artworks"] else "",
                "clearart":      seriesMetadata["artworks"]["hdclearart"]["camo_url"] if seriesMetadata and "artworks" in seriesMetadata and "hdclearart" in seriesMetadata["artworks"] else "",
            })
        return view.create_xbmc_item(self._args, media_info, False)

    def _handle_resume(self):
        """ Handles resuming and updating playhead info back to crunchyroll """

        if self._args.addon.getSetting('sync_playtime') != 'true':
            utils.crunchy_log(self._args, '_handle_resume: Sync playtime not enabled', xbmc.LOGINFO)
            return

        # fetch playhead info from api if not already available
        if not self._args.get_arg('playhead'):
            self._args.set_arg('playhead', 0)
            utils.crunchy_log(self._args, '_handle_resume: fetching playhead info from api', xbmc.LOGINFO)
            playheads = utils.get_playheads_from_api(self._args, self._api, self._args.get_arg('episode_id'))

            if playheads and playheads.get('data'):
                self._args.set_arg('playhead', int(playheads.get(self._args.get_arg('episode_id')).get('playhead')))
                utils.crunchy_log(self._args, "_handle_resume: playhead is %d" % self._args.get_arg('playhead'))

        # wait for video to begin
        if not wait_for_playback(30):
            utils.crunchy_log(self._args, 'Timeout reached, video did not start in 30 seconds', xbmc.LOGERROR)
            return

        # we now set the ResumeTime to kodi, so kodi itself asks the user if he wants to resume. we should no longer
        # need this.

        # ask if user want to continue playback
        # if self._args.get_arg('playhead') and self._args.get_arg('duration'):
        #     resume = int(int(self._args.get_arg('playhead')) / float(self._args.get_arg('duration')) * 100)
        #     if 5 <= resume <= 90:
        #         self._player.pause()
        #         xbmc.sleep(500)
        #         if xbmcgui.Dialog().yesno(self._args.addon_name,
        #                                   self._args.addon.getLocalizedString(30065) % int(resume)):
        #             self._player.seekTime(float(self._args.get_arg('playhead')) - 5)
        #             xbmc.sleep(1000)
        #         self._player.pause()
        # else:
        #     utils.crunchy_log(self._args, "Missing data for resume - playhead: %d" % self._args.get_arg('playhead'))

            # update playtime at crunchyroll in a background thread
        utils.crunchy_log(self._args, "_handle_resume: starting sync thread", xbmc.LOGINFO)
        threading.Thread(target=self.thread_update_playhead).start()

    def _handle_skipping(self):
        """ Handles skipping of video parts (intro, credits, ...) """

        # check whether we have the required data to enable this
        if not self._check_and_filter_skip_data():
            utils.crunchy_log(self._args, "_handle_skipping: required data for skipping is empty", xbmc.LOGINFO)
            return

        # run thread in background to check when whe reach a section where we can skip
        utils.crunchy_log(self._args, "_handle_skipping: starting thread", xbmc.LOGINFO)
        threading.Thread(target=self.thread_check_skipping).start()

    def _handle_upnext(self):
        try:
            if not self._episode_data:
                return
            next_episode = utils.get_upnext_episode(self._args, self._episode_data.episode_id, self._api)
            if not next_episode:
                return
            next_url = view.build_url(
                self._args,
                {
                    "series_id": self._args.get_arg("series_id"),
                    "episode_id": next_episode.episode_id,
                    "stream_id": next_episode.stream_id
                },
                "video_episode_play"
            )
            utils.log("Next URL: %s" % next_url)
            show_next_at_seconds = self._compute_when_episode_ends()
            upnext.send_next_info(self._args, self._episode_data, next_episode, next_url, show_next_at_seconds, self._series_data)
        except Exception:
            utils.crunchy_log(self._args, "Cannot send upnext notification", xbmc.LOGERROR)
    
    def _compute_when_episode_ends(self) -> int:
        if not self._stream_data.skip_events_data:
            return None
        result = None
        skip_events_data = self._stream_data.skip_events_data
        if skip_events_data.get("credits") or skip_events_data.get("preview"):
            video_end = self._episode_data.duration
            credits_start = skip_events_data.get("credits", {}).get("start")
            credits_end = skip_events_data.get("credits", {}).get("end")
            preview_start = skip_events_data.get("preview", {}).get("start")
            preview_end = skip_events_data.get("preview", {}).get("end")
            # If there are outro and preview
            # and if the outro ends when the preview start
            if credits_start and credits_end and preview_start and credits_end == preview_start:
                result = credits_start
            # If there is a preview
            elif preview_start:
                result = preview_start
            # If there is outro without preview
            # and if the outro ends in the last 20 seconds video
            elif credits_start and credits_end and video_end <= credits_end + 20:
                result = credits_start
        return result

    def thread_update_playhead(self):
        """ background thread to update playback with crunchyroll in intervals """

        utils.crunchy_log(self._args, "thread_update_playhead() started", xbmc.LOGINFO)

        try:
            # store playtime of last update and compare before updating, so it won't update while e.g. pausing
            last_updated_playtime = 0

            while self._player.isPlaying() and self._stream_data.stream_url == self._player.getPlayingFile():
                # wait 10 seconds
                xbmc.sleep(10000)

                if (
                        last_updated_playtime < self._player.getTime() and
                        self._player.isPlaying() and
                        self._stream_data.stream_url == self._player.getPlayingFile()
                ):
                    last_updated_playtime = self._player.getTime()
                    # api request
                    try:
                        self._api.make_request(
                            method="POST",
                            url=self._api.PLAYHEADS_ENDPOINT.format(self._api.account_data.account_id),
                            json_data={
                                'playhead': int(self._player.getTime()),
                                'content_id': self._args.get_arg('episode_id')
                            },
                            headers={
                                'Content-Type': 'application/json'
                            }
                        )
                    except (CrunchyrollError, requests.exceptions.RequestException) as e:
                        # catch timeout or any other possible exception
                        utils.crunchy_log(
                            self._args,
                            "Failed to update playhead to crunchyroll: %s for %s" % (
                                str(e), self._args.get_arg('episode_id')
                            )
                        )
                        pass
        except RuntimeError:
            utils.crunchy_log(self._args, 'Playback aborted', xbmc.LOGINFO)

        utils.crunchy_log(self._args, 'thread_update_playhead() has finished', xbmc.LOGINFO)

    def thread_check_skipping(self):
        """ background thread to check and handle skipping intro/credits/... """

        utils.crunchy_log(self._args, 'thread_check_skipping() started', xbmc.LOGINFO)

        while self._player.isPlaying() and self._stream_data.stream_url == self._player.getPlayingFile():
            # do we still have skip data left?
            if len(self._stream_data.skip_events_data) == 0:
                break

            for skip_type in list(self._stream_data.skip_events_data):
                # are we within the skip event timeframe?
                current_time = int(self._player.getTime())
                skip_time_start = self._stream_data.skip_events_data.get(skip_type).get('start')
                skip_time_end = self._stream_data.skip_events_data.get(skip_type).get('end')

                if skip_time_start <= current_time <= skip_time_end:
                    self._ask_to_skip(skip_type)
                    # remove the skip_type key from the data, so it won't trigger again
                    self._stream_data.skip_events_data.pop(skip_type, None)

            xbmc.sleep(1000)

        utils.crunchy_log(self._args, 'thread_check_skipping() has finished', xbmc.LOGINFO)

    def _check_and_filter_skip_data(self) -> bool:
        """ check if data for skipping is present and valid for usage """

        if not self._stream_data.skip_events_data:
            return False

        # if not enabled in config, remove from our list
        if self._args.addon.getSetting("enable_skip_intro") != "true" and self._stream_data.skip_events_data.get(
                'intro'):
            self._stream_data.skip_events_data.pop('intro', None)

        if self._args.addon.getSetting("enable_skip_credits") != "true" and self._stream_data.skip_events_data.get(
                'credits'):
            self._stream_data.skip_events_data.pop('credits', None)

        return len(self._stream_data.skip_events_data) > 0

    def _ask_to_skip(self, section):
        """ Show skip modal """

        utils.crunchy_log(self._args, "_ask_to_skip", xbmc.LOGINFO)

        dialog_duration = (self._stream_data.skip_events_data.get(section, []).get('end', 0) -
                           self._stream_data.skip_events_data.get(section, []).get('start', 0))

        # show only for the first X seconds
        dialog_duration = min(dialog_duration, self._skip_modal_duration_max)

        threading.Thread(
            target=_show_modal_dialog,
            args=[
                SkipModalDialog,
                "plugin-video-crunchyroll-skip.xml"
            ],
            kwargs={
                'seconds': dialog_duration,
                'seek_time': self._stream_data.skip_events_data.get(section).get('end'),
                'label': self._args.addon.getLocalizedString(30015),
                'addon_path': self._args.addon.getAddonInfo("path")
            }
        ).start()


def wait_for_playback(timeout: int = 30):
    """ function that waits for playback """

    timer = time.time() + timeout
    while not xbmc.getCondVisibility("Player.HasMedia"):
        xbmc.sleep(50)
        # timeout to prevent infinite loop
        if time.time() > timer:
            return False

    return True
