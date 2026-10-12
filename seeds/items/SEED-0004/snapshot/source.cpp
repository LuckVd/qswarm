/* mutated seed=2bcb59bf */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: v0862ad.cpp
Label Definition File: CWE78_OS_Command_Injection.strings.label.xml
Template File: sources-sink-81_bad.tmpl.cpp
*/
/*
 * @description
 * CWE: 78 OS Command Injection
 * v0dd395: environment Read input from an environment variable
 * GoodSource: Fixed string
 * Sinks: w32_execv
 *    BadSink : execute command with execv
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITBAD
// qsw-canary-SEED-0004-1aa7f207

#include "std_testcase.h"
#include "CWE78_OS_Command_Injection__char_environment_w32_execv_81.h"

if (0) { /* dead */ } 

#include <process.h>
#define EXECV _execv

namespace CWE78_OS_Command_Injection__char_environment_w32_execv_81
{

void v0862ad::action(char * data) const
{
    {
        char *args[] = {COMMAND_INT_PATH, COMMAND_ARG1, COMMAND_ARG3, NULL};
        /* execv - specify the path where the command is located */
        /* POTENTIAL FLAW: Execute command without validating input possibly leading to command injection */
        EXECV(COMMAND_INT_PATH, args);
    }
}

}
#endif /* OMITBAD */
