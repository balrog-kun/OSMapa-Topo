#! /usr/bin/python3
#
# For tags that have localised versions (e.g. 'name:<lang>' is present for
# 'name') prefer latin alphabets.  This script, for each object and each
# tag, checks if the name is in latin alphabet already.  If it's not, it
# replaces the value with the English ('...:en') version of the same tag,
# if present.  If there's no '...:en' tag, it tries all other language
# suffixes and selects a random one of those that do use the latin alphabet.

import xml.etree.cElementTree as ElementTree
import unicodedata
import sys

def islatin(s):
	count = 0
	total = 0

	for char in s:
		cat = unicodedata.category(char)
		if cat[0] != 'L':
			continue

		name = unicodedata.name(char)
		if 'latin' in name.lower():
			count += 1
		total += 1

	return total and count * 2 >= total

tree = ElementTree.parse(sys.argv[1])

for elem in tree.getroot():
	tags = {}
	for sub in elem:
		if sub.tag != 'tag':
			continue
		v = sub.attrib['v'].strip()
		if v:
			tags[sub.attrib['k']] = ( v, sub )

	for k in tags:
		if 'name' not in k:
			continue
		v, sub = tags[k]
		if ':' in v:
			continue

		if islatin(v):
			continue

		if k + ':en' in tags:
			v2, sub2 = tags[k + ':en']
			sub.attrib['v'] = v2
			continue

		for k2 in tags:
			if not k2.startswith(k + ':'):
				continue
			lang = k2.split(':', 1)[1]
			if lang in [ 'source', 'date' ]:
				continue

			v2, sub2 = tags[k2]
			if islatin(v2):
				sub.attrib['v'] = v2
				break

tree.write(sys.argv[1] + '-latin.osm', 'utf-8')
