import datetime as dt
import random
import unittest

import memory_provenance as provenance


def previous_parse(value):
    if type(value) is not str or not provenance._UTC_MILLIS_RE.fullmatch(value):
        return None
    try:
        return dt.datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


class TimestampLookup(unittest.TestCase):
    def test_calendar_and_id_match_the_previous_validator(self):
        stamps = ['0000-01-01T00:00:00.000Z', '0001-02-03T04:05:06.007Z',
                  '0999-02-03T04:05:06.007Z', '2024-02-29T23:59:59.999Z',
                  '2025-02-29T00:00:00.000Z', '2026-13-01T00:00:00.000Z',
                  '2026-01-01T24:00:00.000Z', '2026-01-01T00:00:60.000Z',
                  '２０２６-01-01T00:00:00.000Z', '2026-01-01T00:00:00.０００Z',
                  '2026-01-01T00:00:00.00Z', '2026-01-01T00:00:00.000z', None]
        rng = random.Random(1711)
        origin = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        for _ in range(300):
            offset = dt.timedelta(milliseconds=rng.randrange(1, 10**12))
            stamps.append((origin + offset).isoformat(timespec='milliseconds').replace('+00:00', 'Z'))

        for stamp in stamps:
            previous = previous_parse(stamp)
            self.assertEqual(provenance._utc_millis(stamp), previous, stamp)
            for pattern, kind in ((provenance._EVENT_ID_RE, 'evt'), (provenance._COMPACT_ID_RE, 'cmp')):
                for millis in ('007', '000', '999'):
                    value = f'{kind}-20260203T040506{millis}438Z-12345678'
                    match = pattern.fullmatch(value)
                    expected = previous is not None and match is not None and (
                        match.group('date') == previous.strftime('%Y%m%d')
                        and match.group('clock') == previous.strftime('%H%M%S')
                        and match.group('micros')[:3] == previous.strftime('%f')[:3]
                    )
                    self.assertEqual(provenance._id_matches_timestamp(value, stamp, pattern), expected)
            if previous and previous.year >= 1000:
                digits = previous.strftime('%Y%m%dT%H%M%S%f')
                self.assertTrue(provenance._id_matches_timestamp(f'evt-{digits}Z-12345678', stamp,
                                                                provenance._EVENT_ID_RE))


if __name__ == '__main__':
    unittest.main()
