#! /usr/bin/env python
# -*- coding: utf-8 -*-
# OpenStreetMap route extraction tool.
# The supported tags are based on earlier scripts written by
# Radek Barton / www.opentrackmap.cz and pbabik's version of that script.

from getopt import *
from sys import exit, argv
from psycopg2 import *

def usage():
    print("copy_tracks_simple.py [-n <db_name>]")

# Parse script arguments.
try:
    opts, args = getopt(argv[1:], "n:", ["db-name"])
except GetoptError as error:
    print(str(error))
    usage()
    exit(2)

# Default script argumetns values.
db_name = "osm"

for opt, val in opts:
    if opt in ("-n", "--db-name"):
        db_name = val
    else:
        assert False, "unhandled option"

# Create connection to DB server.
connection = connect("dbname='%s' user='osm' " % (db_name));
cursor = connection.cursor()

# Clean previous tracks.
cursor.execute("BEGIN")
cursor.execute("DROP TABLE IF EXISTS planet_osm_track CASCADE")

# These are used to deal with postgresql's unnest() behaviour on two-
# dimensional arrays and the inability to see an array of arrays as
# a two-dimensional array.
cursor.execute("""CREATE OR REPLACE FUNCTION public.reduce_dim(anyarray)
RETURNS SETOF anyarray AS
$function$
DECLARE
  s $1%type;
BEGIN
  FOREACH s SLICE 1 IN ARRAY $1 LOOP
    RETURN NEXT s;
  END LOOP;
  RETURN;
END;
$function$
LANGUAGE plpgsql IMMUTABLE""")
# Note: this is slow (quadratic complexity)
cursor.execute("DROP AGGREGATE IF EXISTS array_cat_agg(anyarray)")
cursor.execute("""CREATE AGGREGATE array_cat_agg(anyarray)
    (sfunc = array_cat, stype = anyarray, initcond = '{}')""")

# The type for the "routes" column.
cursor.execute("DROP TYPE IF EXISTS rt CASCADE")
cursor.execute("CREATE TYPE rt AS (colour TEXT, route TEXT)")

# kct_* and marked_trail_* tags present in our osm2pgsql .style file
tag_colours = [ "yellow", "red", "green", "blue", "black" ]

known_routes = "('hiking', 'bicycle', 'mtb', 'horse', 'foot', 'ski', " + \
  "'piste', 'inline_skates')"

# Create temporary table that selects only relevant rows and columns and
# transforms Slovak, German and Czech tagging styles to simple "colour"
# and "route" values.
print("SELECTing all coloured route IDs")
cursor.execute("""CREATE TEMPORARY TABLE tmp_planet_osm_track AS (
  SELECT osm_id, name, (
    SELECT array_agg(DISTINCT row(r.colour,
      CASE
        WHEN r.route IN """ + known_routes + """ THEN r.route
        WHEN t.route IN """ + known_routes + """ THEN t.route
        WHEN network IN ('lcn', 'rcn', 'ncn') THEN 'bicycle'
        WHEN network IN ('lwn', 'rwn', 'nwn') THEN 'hiking'
        ELSE 'yes'
      END)::rt)
    FROM unnest(t.route_array) AS r
    -- Check format - may still give us mapnik errors, should check colour names
    WHERE r.colour ~ '#[0-9a-fA-F]{3,6}' OR r.colour ~ '[a-z]+') AS routes
  FROM (
    SELECT -osm_id AS osm_id, route, network, route_name AS name,
      CASE
        WHEN colour IS NOT NULL THEN ARRAY[row(colour, NULL)::rt]
        WHEN marked_trail IS NOT NULL THEN ARRAY[row(marked_trail, NULL)::rt]
        WHEN "osmc:symbol" LIKE 'white:%:white%' THEN
          ARRAY[row(
            substring("osmc:symbol" from 'white:#"%#":white%' for '#'),
            NULL)::rt]
        WHEN "osmc:symbol" ~ '(.+):(white|orange|yellow):\\1_.+' THEN
          ARRAY[row(
            substring("osmc:symbol" from '(.+):white:\\1_.+'),
            CASE
              WHEN "osmc:symbol" LIKE '%_backslash' THEN 'learning'
              WHEN "osmc:symbol" LIKE '%_L' THEN 'ruin'
              WHEN "osmc:symbol" LIKE '%_triangle' THEN 'peak'
              WHEN "osmc:symbol" LIKE '%_bowl' THEN 'spring'
              WHEN "osmc:symbol" LIKE '%_turned_T' THEN 'interesting_object'
              WHEN "osmc:symbol" LIKE '%_dot' THEN 'horse'
              WHEN "osmc:symbol" LIKE '%:orange:%' THEN 'bicycle'
              WHEN "osmc:symbol" LIKE '%:yellow:%' THEN 'ski'
              ELSE NULL
            END)::rt]
        -- TODO: handle "white:colour_bar"
        ELSE""" + \
    " || ".join([ """
          CASE WHEN kct_""" + c + """ IS NOT NULL THEN
            ARRAY[row('""" + c + "', kct_" + c + """)::rt]
	    ELSE ARRAY[]::rt[] END ||
          CASE WHEN marked_trail_""" + c + """ IS NOT NULL THEN
            ARRAY[row('""" + c + "', marked_trail_" + c + """)::rt]
	    ELSE ARRAY[]::rt[] END""" \
    for c in tag_colours ]) + """
      END AS route_array
    FROM planet_osm_line
    WHERE COALESCE(""" +
      ", ".join([ 'kct_' + c for c in tag_colours ]) +
      ", marked_trail, " +
      ", ".join([ 'marked_trail_' + c for c in tag_colours ]) +
      """, \"osmc:symbol\", colour) IS NOT NULL) AS t)""")
#cursor.execute("CREATE INDEX tmp_idx ON tmp_planet_osm_track(osm_id)")

print("Now finding relation parts")
cursor.execute("""CREATE TEMPORARY TABLE tmp2_planet_osm_track AS
  SELECT osm_id, routes, name, parts
  FROM tmp_planet_osm_track, planet_osm_rels WHERE osm_id = id AND
    array_length(tmp_planet_osm_track.routes, 1) > 0""")
#cursor.execute("CREATE INDEX tmp2_idx ON tmp2_planet_osm_track USING gin (parts)")

print("Grouping colours by ways")
###cursor.execute("""CREATE TEMPORARY TABLE tmp3_planet_osm_track AS
cursor.execute("DROP TABLE IF EXISTS tmp3_planet_osm_track CASCADE")
cursor.execute("""CREATE TABLE tmp3_planet_osm_track AS
  SELECT osm_id,
    -- Note: this magically correctly skips over NULLs:
    array_to_string(array_agg(DISTINCT relln.name), '   --   ') AS name,
    -- This is basically array_cat_agg(DISTINCT routes) AS routes
    (SELECT array_agg(DISTINCT r)
       FROM unnest(array_cat_agg(routes)) AS r) AS routes
  FROM (
    SELECT unnest(parts) AS osm_id, routes, name
    FROM tmp2_planet_osm_track) AS relln
  GROUP BY osm_id""")
print("Analyzing the ways table")
cursor.execute("ANALYZE tmp3_planet_osm_track")

cursor.execute("DELETE FROM geometry_columns WHERE f_table_name = \
  'planet_osm_track'")
cursor.execute("INSERT INTO geometry_columns (f_table_catalog, f_table_schema, \
  f_table_name, f_geometry_column, coord_dimension, srid, type) SELECT \
  f_table_catalog, f_table_schema, 'planet_osm_track', f_geometry_column, \
  coord_dimension, srid, type FROM geometry_columns WHERE f_table_name = \
  'planet_osm_line'")

print("Analyzing planet_osm_line")
cursor.execute("ANALYZE planet_osm_line")
cursor.execute("COMMIT")###

print("Adding geometries to ways")
# TODO: add the potential routes tagged as ways here? RIGHT OUTER JOIN(?)
cursor.execute("CREATE TABLE planet_osm_track AS " +
  "SELECT way, tmp3_planet_osm_track.* " +
  "FROM tmp3_planet_osm_track JOIN planet_osm_line USING (osm_id)")
cursor.execute("COMMIT")###

print("Indexing the geometries")
cursor.execute("CREATE INDEX planet_osm_track_idx ON planet_osm_track " +
  "USING GIST (way)")
cursor.execute("ANALYZE planet_osm_track")

cursor.execute("COMMIT")
cursor.close()
connection.commit()
