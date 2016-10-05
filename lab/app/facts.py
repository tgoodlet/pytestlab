import operator
from cliff.lister import Lister
import logging
import copy


class FactsLister(Lister):
    "manage facts for machines"
    log = logging.getLogger(__name__)

    def get_parser(self, prog_name):
        parser = super(FactsLister, self).get_parser(prog_name)
        parser.add_argument(
            'host', type=str,
            help='software location or environment name (requires --env)'
        )
        parser.add_argument('key', nargs='?', type=str)
        parser.add_argument('value', nargs='?', type=str)
        parser.add_argument('--env', action='store_true')
        parser.add_argument('--unset', action='store')
        parser.add_argument('--unset-all', action='store_true')
        return parser

    def take_action(self, parsed_args):
        name = parsed_args.host

        def process(parsed_args, entry):
            """Process the model entry based on parsed_args.
            """
            if parsed_args.unset:
                try:
                    del entry[parsed_args.unset]
                except KeyError:
                    pass
            elif parsed_args.unset_all:
                entry.clear()
            elif parsed_args.key and parsed_args.value:
                entry[parsed_args.key] = parsed_args.value

            items = sorted(entry.view.items(), key=operator.itemgetter(0))
            if not items:
                self.log.warn("The location {} currently has no facts"
                              .format(name))
            return ('key', 'value'), items

        if not parsed_args.env:
            equip = self.app.get_equipment(name)
            return process(parsed_args, equip)

        # an environment was requested
        assert parsed_args.env
        env = self.app.get_environment(name)
        if not env:
            self.log.warn(
                "No environment with name '{}' exists?"
                .format(name)
            )
            return ('key', 'value'), []

        # batch process over all equipment in the environment
        args = copy.copy(parsed_args)
        for rolename, hosts in sorted(
            env.view.iteritems(), key=operator.itemgetter(0)
        ):
            self.log.info("\n|{}|\n".format(rolename))
            for hostname in hosts:
                self.log.info("{}".format(hostname))
                args.env = False
                args.host = hostname
                self.run(args)

        # calls to self.run() above to all the work
        return ('key', 'value'), []