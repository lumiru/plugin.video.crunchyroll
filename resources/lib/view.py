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

import re
try:
    from urllib import quote_plus
except ImportError:
    from urllib.parse import quote_plus

import xbmc
import xbmcvfs
import xbmcgui
import xbmcplugin


# keys allowed in setInfo
types = ["count", "size", "date", "genre", "country", "year", "episode", "season", "sortepisode", "top250", "setid",
         "tracknumber", "rating", "userrating", "watched", "playcount", "overlay", "cast", "castandrole", "director",
         "mpaa", "plot", "plotoutline", "title", "originaltitle", "sorttitle", "duration", "studio", "tagline", "writer",
         "tvshowtitle", "premiered", "status", "set", "setoverview", "tag", "imdbnumber", "code", "aired", "credits",
         "lastplayed", "album", "artist", "votes", "path", "trailer", "dateadded", "mediatype", "dbid"]


def endofdirectory(args, content_type = None):
    if content_type is not None:
        xbmcplugin.setContent(int(args._argv[1]), content_type)

    # sort methods are required in library mode
    xbmcplugin.addSortMethod(int(args._argv[1]), xbmcplugin.SORT_METHOD_NONE)

    # let xbmc know the script is done adding items to the list
    xbmcplugin.endOfDirectory(handle = int(args._argv[1]))


def add_item(args, info, isFolder=True, total_items=0, mediatype="video"):
    """Add item to directory listing.
    """

    # create list item
    li = xbmcgui.ListItem(label = info["title"])

    # get infoLabels
    infoLabels = make_infolabel(args, info)

    # get url
    u = build_url(args, info)

    if isFolder:
        # directory
        infoLabels["mediatype"] = "tvshow"
        if "mediatype" in info:
            infoLabels["mediatype"] = info["mediatype"]
        li.setInfo(mediatype, infoLabels)
    else:
        # playable video
        infoLabels["mediatype"] = "episode"
        li.setInfo(mediatype, infoLabels)
        li.setProperty("IsPlayable", "true")

        # add context menue
        cm = []
        if u"series_id" in info:
            cm.append((args._addon.getLocalizedString(30045), "Container.Update(%s)" % build_url(args, { "mode": "series", "series_id": info["series_id"] })))
        if u"collection_id" in info and u"season" in info:
            cm.append((args._addon.getLocalizedString(30046), "Container.Update(%s)" % build_url(args, { "mode": "series", "series_id": info["series_id"], "season": info["season"], "collection_id": info["collection_id"] })))
        if len(cm) > 0:
            li.addContextMenuItems(cm)

    # set media image
    artworks = {}
    if "thumb" in info:
        artworks["thumb"] = info["thumb"]
    if "poster" in info:
        artworks["poster"] = info["poster"]
        artworks["banner"] = info["poster"]
        artworks["icon"] = info["poster"]
    if "clearart" in info:
        artworks["clearart"] = info["clearart"]
    if "clearlogo" in info:
        artworks["clearlogo"] = info["clearlogo"]
    if "fanart" in info:
        artworks["fanart"] = info["fanart"]
    li.setArt(artworks)

    # set media image
    #li.setArt({"thumb":  info.get("thumb",  "DefaultFolder.png"),
    #           "poster": info.get("poster",  "DefaultFolder.png"),
    #           "banner": info.get("poster",  "DefaultFolder.png"),
    #           "clearart": info.get("clearart",  ""),
    #           "clearlogo": info.get("clearlogo",  ""),
    #           "fanart": info.get("fanart",  xbmcvfs.translatePath(args._addon.getAddonInfo("fanart"))),
    #           "icon":   info.get("poster",  "DefaultFolder.png")})

    # add item to list
    xbmcplugin.addDirectoryItem(handle     = int(args._argv[1]),
                                url        = u,
                                listitem   = li,
                                isFolder   = isFolder,
                                totalItems = total_items)


def quote_value(value, PY2):
    """Quote value depending on python
    """
    if PY2:
        if not isinstance(value, basestring):
            value = str(value)
        return quote_plus(value.encode("utf-8") if isinstance(value, unicode) else value)
    else:
        if not isinstance(value, str):
            value = str(value)
        return quote_plus(value)

avoid_url_args = [ "mode", "title", "genre", "episode_id", "series_id", "season", "collection_id", "search" ]
whitelist_url_args = [  ]

def build_url(args, info):
    """Create url
    """
    path = "/"
    if "mode" in info:
        path = "/menu/" + info["mode"]
        if info["mode"] == "series":
            path = "/series/" + info["series_id"]
        elif info["mode"] == "episodes":
            path = "/series/" + info["series_id"] + "/" + str(info["season"]) + "/" + info["collection_id"]
        elif info["mode"] == "videoplay":
            path = "/video/" + info["episode_id"]
        elif "genre" in info or hasattr(args, "genre"):
            path = path + "/" + (info["genre"] if "genre" in info else args.genre)
            if "search" in info and info["search"]:
                path = path + "/" + quote_value(info["search"], args.PY2)
        if "offset" in info:
            path = path + "/offset/" + str(info["offset"])
    s = ""

    # step 1 copy new information from info
    for key, value in list(info.items()):
        if key in whitelist_url_args and value:
            s = s + "&" + key + "=" + quote_value(value, args.PY2)

    # step 2 copy old information from args, but don't append twice
    for key, value in list(args.__dict__.items()):
        if key in whitelist_url_args and value and key in types and not "&" + str(key) + "=" in s:
            s = s + "&" + key + "=" + quote_value(value, args.PY2)

    if len(s) > 0:
        s = "?" + s[1:]

    result = args._addonurl + path + s

    return result


def make_infolabel(args, info):
    """Generate infoLabels from existing dict
    """
    infoLabels = {}
    # step 1 copy new information from info
    for key, value in list(info.items()):
        if value and key in types:
            infoLabels[key] = value

    # step 2 copy old information from args, but don't overwrite
    for key, value in list(args.__dict__.items()):
        if value and key in types and key not in infoLabels:
            infoLabels[key] = value

    return infoLabels
