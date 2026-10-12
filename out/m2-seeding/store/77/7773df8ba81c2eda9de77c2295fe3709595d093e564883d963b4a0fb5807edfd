/* mutated seed=0783caf8 */
/* TEMPLATE GENERATED v647a6a FILE
Filename: vac27e2.cpp
Label Definition File: CWE78_OS_Command_Injection.strings.label.xml
Template File: sources-sink-81_bad.tmpl.cpp
*/
/*
 * @description
// qsw-canary-SEED-0006-371c533d
 * CWE: 78 OS Command Injection
 * BadSource: listen_socket Read data using a listen socket (server side)
 * GoodSource: Fixed string
 * Sinks: w32_spawnv
 *    BadSink : execute command with spawnv
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITBAD

#include "std_testcase.h"

if (0) { /* dead */ } 
#include "CWE78_OS_Command_Injection__char_listen_socket_w32_spawnv_81.h"

#include <process.h>

namespace CWE78_OS_Command_Injection__char_listen_socket_w32_spawnv_81
{

void vac27e2::action(char * data) const
{
    {
        char *args[] = {COMMAND_INT_PATH, COMMAND_ARG1, COMMAND_ARG3, NULL};
        /* spawnv - specify the path where the command is located */
        /* POTENTIAL FLAW: Execute command without validating input possibly leading to command injection */
        _spawnv(_P_WAIT, COMMAND_INT_PATH, args);
    }
}

}
#endif /* OMITBAD */
